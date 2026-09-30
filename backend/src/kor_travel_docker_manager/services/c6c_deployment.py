from __future__ import annotations

import fcntl
import grp
import hashlib
import hmac
import http.cookiejar
import json
import os
import posixpath
import re
import shlex
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from io import StringIO
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final, Literal, TypeVar, cast
from urllib.parse import SplitResult, unquote, urlsplit

from dotenv import dotenv_values

# **모듈 경유로 부른다**(GM-17 A 적대 리뷰 H-1). `from ... import load_compose_bind_allowlist`
# 로 이름을 당겨오면 배포 경로와 로더의 연결이 이 import 한 줄에만 있고, 그 줄이
# 끊기거나 이름이 다른 것으로 바뀌어도 어떤 검사도 세지 못한다 — 리뷰어가 로더를
# 코드 안 얼린 dict로 갈아끼운 고장난 구현에서 전체 스위트 초록을 재현했다.
# 모듈 속성으로 부르면 그 연결 자체를 테스트가 결박할 수 있다.
from kor_travel_docker_manager.services import registry as registry_module
from kor_travel_docker_manager.services.capabilities import (
    _MANAGED_COMPOSE_MUTATION_CAPABILITY,
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
)
from kor_travel_docker_manager.services.compose_references import (
    assert_protected_references_are_derived,
    assert_resolved_secret_values_stay_at_reference_sites,
    secret_values_for,
    variable_names,
)
from kor_travel_docker_manager.services.errors import (
    ComposeCandidateContractError,
    ComposePostMutationContractError,
    DeploymentContractError,
    ManagerMutationActiveError,
)
from kor_travel_docker_manager.services.loopback_readiness import (
    LOOPBACK_HTTP_READINESS_ATTEMPTS,
    LOOPBACK_HTTP_READINESS_RETRY_SECONDS,
)
from kor_travel_docker_manager.services.map_service_contract import (
    C6C_CANCEL_PROBE_CAPABILITY_GENERATION,
)
from kor_travel_docker_manager.services.registry import (
    get_targets_config_path,
    load_targets_config,
)
from kor_travel_docker_manager.services.runtime_topology import (
    MAP_TARGET,
    PINVI_TARGET,
    DagsterFamily,
    LazyMapping,
    LazySequence,
    LazySet,
    dagster_family,
    installed_container_name,
    runtime_topology,
)
from kor_travel_docker_manager.services.trusted_install import (
    GLOBAL_MUTATION_LOCK_FD_ENV,
    GLOBAL_MUTATION_LOCK_PATH,
    TRUSTED_INSTALL_ROOT,
    TRUSTED_STATE_ROOT,
)

_MAP_API_SERVICE = "kor-travel-map-api"
# Map API의 기본 실행 경계는 Compose override가 아니라 이미지 Dockerfile에
# 봉인된다. Map release는 ENTRYPOINT를 절대 경로로 고정하고 CMD를 비워 둔다.
_MAP_API_IMMUTABLE_ENTRYPOINT = ["/app/docker/api-entrypoint.sh"]
_MAP_API_IMMUTABLE_COMMAND = None
# 관측 카드(source_status)가 같은 값을 두 번 적지 않게 공개 별칭만 준다 — 값을
# 복제하면 화면이 보여 주는 기대 계약과 실제 강제 지점이 조용히 갈라진다. 이 계약은
# 실제로 사흘 만에 정반대로 뒤집힌 적이 있어서(ENTRYPOINT↔CMD) 특히 위험하다.
MAP_API_IMMUTABLE_ENTRYPOINT: Final[tuple[str, ...]] = tuple(_MAP_API_IMMUTABLE_ENTRYPOINT)
MAP_API_IMMUTABLE_COMMAND: Final[None] = _MAP_API_IMMUTABLE_COMMAND
_MAP_UI_SERVICE = "kor-travel-map-ui"
_CONCIERGE_API_SERVICE = "kor-travel-concierge-api"
_CONCIERGE_UI_SERVICE = "kor-travel-concierge-ui"
_MAP_DAGSTER_STORAGE_MIGRATE_SERVICE = "kor-travel-map-dagster-storage-migrate"
_MAP_DB_ROLE_BOOTSTRAP_SERVICE = "kor-travel-map-db-role-bootstrap"
#: ADR-101: root migration과 finalize 두 one-shot이 하나로 접혔다. Map 이미지의
#: `ktm-application-schema-fresh-300` / `-fresh-finalize`가 삭제됐고, 그 둘이
#: 나눠 하던 일은 revision `400`과 `kortravelmap.infra.runtime_privileges`가 한다.
_MAP_APPLICATION_SCHEMA_SERVICE = "kor-travel-map-application-schema"
_PINVI_API_SERVICE = "pinvi-api"
_PINVI_ADMIN_BOOTSTRAP_SERVICE = "pinvi-admin-bootstrap"
#: 모든 PostgreSQL이 **같은** 초기화 인증 인자를 쓴다. 공유 상수로 두는 이유는 한쪽만
#: 바뀌는 것을 막기 위해서다 — 2026-09-17 감사가 실측했듯 Map 쪽은 이 값이 계약에
#: 없어서 `--auth-host=trust`(fresh PGDATA에서 superuser 인증을 통째로 끄는 값)가
#: raw·resolved·UI 저장 경로를 **전부 통과**했다.
_POSTGRES_CANONICAL_INITDB_ARGS = "--auth-host=scram-sha-256"
#: ADR-46 — PinVi 앱(`pinvi`)·Dagster(`pinvi_dagster`) DSN은 공용 제어 평면
#: instance(`kor-travel-shared-postgres`)를 쓴다. PinVi API, PinVi Dagster family(webserver·
#: code-server·daemon), admin bootstrap이 실제로 접속하는 DSN의 포트다. 옛 전용
#: instance(pinvi-postgres, :12800)는 2026-09-28에
#: compose에서 뺐다.
_PINVI_SHARED_POSTGRES_PORT = 11000
_PINVI_WEB_SERVICE = "pinvi-web"
_PINVI_DATABASE_URL_ENV = "PINVI_DATABASE_URL"
_PINVI_DAGSTER_PG_URL_ENV = "PINVI_DAGSTER_PG_URL"
_PINVI_APP_DB_USER_ENV = "PINVI_APP_DB_USER"
_PINVI_APP_DB_PASSWORD_ENV = "PINVI_APP_DB_PASSWORD"


# ── Dagster family 이름(ADR-54) ─────────────────────────────────────────────
# Map·PinVi Dagster 서비스 이름은 literal로 두지 않는다. 공용 plane 전환이 옛 webserver·daemon을
# `legacy-dagster`로 내리면 frozen render에서 사라지는데, literal 집합은 그것을 여전히 요구하고
# 검사한다. 이름은 설치된 release의 compose·targets에서 파생한다(`runtime_topology`).
#
# 두 종류로 나뉜다. **env 계약**(DSN·Geo key 값 고정, UI 잠금)은 서비스가 문서에 있으면 보는 것이라
# 스위치와 무관하게 family 전부(`processes`)에 건다 — profile로 내려간 옛 서비스도 켜면 그 값으로
# 돈다. **실행·필수 집합**(required, 런타임, secret isolation)은 스위치를 따른다 — `shared`면 옛
# webserver·daemon은 빠지고 carrier인 code-server가 들어간다.


def _map_dagster() -> DagsterFamily:
    return dagster_family(MAP_TARGET)


def _pinvi_dagster() -> DagsterFamily:
    return dagster_family(PINVI_TARGET)


def _map_dagster_runtime_services() -> tuple[str, ...]:
    """지금 떠 있어야 할 Map Dagster slot 서비스 — `own`이면 webserver·daemon, `shared`면 code-server."""

    return runtime_topology().services_for(("map_dagster", "map_dagster_daemon"))


#: mutation-identifier 분류용 Map runtime slot 서비스(slot 순서).
_MAP_RUNTIME_SERVICES: Final[LazySequence[str]] = LazySequence(
    lambda: runtime_topology().services_for(
        ("map_api", "map_ui", "map_dagster", "map_dagster_daemon")
    )
)


def _map_dagster_secret_isolation_containers() -> tuple[str, ...]:
    """Geo key를 받는 Map Dagster 컨테이너 — 지금 떠 있는 family 프로세스(carrier·daemon·code-server)."""

    family = _map_dagster()
    services = dict.fromkeys(
        name for name in (family.carrier, family.active_daemon, family.code_server) if name
    )
    return tuple(installed_container_name(name) for name in services)
# GM-09: 정본은 services/trusted_install.py다. ADR-51 C-1부터 cli.py는 경로 별칭을
# 두지 않고 `manager_mutation_lock()`으로만 이 lock을 잡는다 — pinned rebuild와 pin
# 회전이 서로 직렬화되는 근거가 이 한 이름이다.
_C6C_GLOBAL_MUTATION_LOCK = GLOBAL_MUTATION_LOCK_PATH
# 위 lock 디렉터리·파일의 기대 소유자. 운영에서는 항상 root(0)다. 테스트는 lock을
# 자기 소유의 tmp 디렉터리로 옮기면서 이 값만 자기 euid로 바꾼다 — 0600·nlink 1·
# dev/ino·디렉터리 0700 검사는 그대로 돈다.
_GLOBAL_LOCK_OWNER_UID: int = 0
# GM-09: 경로 상수의 정본은 services/trusted_install.py다.
_DEFAULT_C6C_PRODUCTION_STATE_ROOT = TRUSTED_STATE_ROOT
_C6C_PRODUCTION_STATE_ROOT = _DEFAULT_C6C_PRODUCTION_STATE_ROOT
_MAP_READ_ENV = "KOR_TRAVEL_MAP_API_OPS_READ_TOKEN"
_MAP_CANCEL_ENV = "KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN"
_MAP_FIXTURE_ENV = "KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN"
_MAP_REQUIRED_ENV = "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED"
_PINVI_READ_ENV = "PINVI_KOR_TRAVEL_MAP_OPS_READ_TOKEN"
_PINVI_CANCEL_ENV = "PINVI_KOR_TRAVEL_MAP_OPS_CANCEL_TOKEN"
_MAP_UI_USERNAME_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME"
_MAP_UI_PASSWORD_HASH_ENV = "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH"
_MAP_UI_SESSION_SECRET_ENV = "KOR_TRAVEL_MAP_UI_SESSION_SECRET"
_MAP_ADMIN_PROXY_ENV = "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET"
_MAP_SERVICE_TOKEN_ENV = "KOR_TRAVEL_MAP_API_SERVICE_TOKEN"
_MAP_CURSOR_SIGNING_SECRET_ENV = "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET"
_MAP_METRICS_TOKEN_ENV = "KOR_TRAVEL_MAP_API_METRICS_TOKEN"
_MAP_GEO_API_KEY_SOURCE_ENV = "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY"
_MAP_UI_GEO_API_KEY_ENV = "KOR_TRAVEL_GEO_API_KEY"
_MAP_CURATION_SNAPSHOT_DIGEST_ENV = (
    "KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256"
)
_MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV = (
    "KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256"
)
# T-VN-M01 manual Feature 생성 credential은 PinVi curation pair와 별개다. 원문은
# Map UI server runtime에만, digest는 Map API에만 전달하며 두 값의 파생 관계를
# Manager가 frozen Compose 전에 확인한다.
_MAP_FEATURE_CREATE_TOKEN_ENV = "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN"
_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV = (
    "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256"
)
_MAP_FEATURE_CREATE_ENABLED_ENV = (
    "KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED"
)
_MAP_CACHE_TARGET_PRINCIPALS_ENV = (
    "KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS"
)
_PINVI_CURATION_SNAPSHOT_ENV = "PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN"
_PINVI_CUTOVER_MAPPING_ENV = "PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN"
_MAP_PROFILE_ENV = "KOR_TRAVEL_MAP_API_PROFILE"
_MAP_PUBLIC_API_KEY_REQUIRED_ENV = "KOR_TRAVEL_MAP_API_PUBLIC_API_KEY_REQUIRED"
_MAP_DEBUG_ROUTES_ENABLED_ENV = "KOR_TRAVEL_MAP_API_DEBUG_ROUTES_ENABLED"
_MAP_FEATURES_ROUTES_ENABLED_ENV = "KOR_TRAVEL_MAP_API_FEATURES_ROUTES_ENABLED"
_MAP_DESTRUCTIVE_ENABLED_ENV = "KOR_TRAVEL_MAP_API_DESTRUCTIVE_ENABLED"
_MAP_PROMETHEUS_METRICS_ENABLED_ENV = (
    "KOR_TRAVEL_MAP_API_PROMETHEUS_METRICS_ENABLED"
)
_MAP_ADMIN_TRUSTED_PROXY_CIDRS_ENV = (
    "KOR_TRAVEL_MAP_API_ADMIN_TRUSTED_PROXY_CIDRS"
)
_MAP_UI_PASSWORD_ENV = "KTDM_C6C_MAP_UI_ADMIN_PASSWORD"
_MAP_UI_PROTECTED_PATH = "/ops/datasets"
_PINVI_ADMIN_PASSWORD_ENV = "KTDM_C6C_PINVI_ADMIN_PASSWORD"
_CONCIERGE_UI_BACKEND_ORIGIN_ENV = "BACKEND_ORIGIN"
_CONCIERGE_UI_BACKEND_API_KEY_ENV = "BACKEND_API_KEY"
_CONCIERGE_UI_VWORLD_KEY_ENV = "NEXT_PUBLIC_VWORLD_SERVICE_KEY"
_CONCIERGE_UI_PUBLIC_API_BASE_ENV = "NEXT_PUBLIC_API_BASE_URL"
_CONCIERGE_UI_ADMIN_USERNAME_ENV = "KTC_ADMIN_USERNAME"
_CONCIERGE_UI_ADMIN_PASSWORD_HASH_ENV = "KTC_ADMIN_PASSWORD_HASH"
_CONCIERGE_UI_SESSION_SECRET_ENV = "KTC_UI_SESSION_SECRET"
_CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV = "KTC_ADMIN_PROXY_SECRET"
_CONCIERGE_UI_TRUST_FORWARDED_IPS_ENV = "KTC_UI_TRUST_FORWARDED_IPS"
_CONCIERGE_UI_PUBLIC_ORIGINS_ENV = "KTC_UI_PUBLIC_ORIGINS"
_CONCIERGE_ROOT_BACKEND_API_KEY_ENV = "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY"
_CONCIERGE_ROOT_API_KEYS_ENV = "KOR_TRAVEL_CONCIERGE_API_KEYS"
_CONCIERGE_ROOT_APP_ENV = "KOR_TRAVEL_CONCIERGE_APP_ENV"
_CONCIERGE_ROOT_API_AUTH_ENABLED_ENV = "KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED"
_CONCIERGE_ROOT_VWORLD_KEY_ENV = "KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY"
_CONCIERGE_ROOT_ADMIN_USERNAME_ENV = "KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME"
_CONCIERGE_ROOT_ADMIN_PASSWORD_HASH_ENV = "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH"
_CONCIERGE_ROOT_SESSION_SECRET_ENV = "KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET"
_CONCIERGE_ROOT_PROXY_SECRET_ENV = "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET"
_CONCIERGE_ROOT_TRUST_FORWARDED_IPS_ENV = "KOR_TRAVEL_CONCIERGE_UI_TRUST_FORWARDED_IPS"
_CONCIERGE_ROOT_PUBLIC_ORIGINS_ENV = "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_ORIGINS"
_CONCIERGE_ROOT_PUBLIC_API_BASE_ENV = "KOR_TRAVEL_CONCIERGE_UI_PUBLIC_API_BASE_URL"
_CONCIERGE_FIXED_BACKEND_ORIGIN = "http://127.0.0.1:12601"
_CONCIERGE_CANONICAL_RAW_NETWORK_MODE = "${KTDM_DOCKER_NETWORK_MODE:-host}"
_CONCIERGE_CANONICAL_RESOLVED_NETWORK_MODE = "host"
_CONCIERGE_API_CANONICAL_RAW_COMMAND = (
    "python",
    "-m",
    "ktc.cli",
    "api",
    "--host",
    "0.0.0.0",
    "--port",
    "${KOR_TRAVEL_CONCIERGE_API_PORT:-12601}",
)
_CONCIERGE_API_CANONICAL_RESOLVED_COMMAND = (
    "python",
    "-m",
    "ktc.cli",
    "api",
    "--host",
    "0.0.0.0",
    "--port",
    "12601",
)
_CONCIERGE_UI_CANONICAL_RAW_COMMAND = (
    "sh",
    "-ec",
    '[ -n "$$KTC_ADMIN_PASSWORD_HASH" ] && [ -n "$$KTC_UI_SESSION_SECRET" ] && '
    '[ "$${#KTC_UI_SESSION_SECRET}" -ge 32 ] && [ -n "$$KTC_ADMIN_PROXY_SECRET" ] && '
    '[ "$${#KTC_ADMIN_PROXY_SECRET}" -ge 32 ] || { echo "FATAL concierge-ui auth env invalid" >&2; exit 1; }; '
    "npm run build && exec npm run start -- -H 0.0.0.0 -p ${KOR_TRAVEL_CONCIERGE_UI_PORT:-12605}",
)
_FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES = frozenset(
    {
        "KOR_TRAVEL_MAP_DATA_GO_KR_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KMA_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KMA_APIHUB_KEY",
        "KOR_TRAVEL_MAP_API_OPINET_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_DATAGOKR_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_VISITKOREA_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KREX_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KNPS_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_AIRKOREA_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_KRFOREST_SERVICE_KEY",
        "KOR_TRAVEL_MAP_API_ETL_LIVE_PREVIEW_ENABLED",
        "KOR_TRAVEL_MAP_KAKAO_LOCAL_REST_API_KEY",
        "KOR_TRAVEL_MAP_NAVER_SEARCH_CLIENT_ID",
        "KOR_TRAVEL_MAP_NAVER_SEARCH_CLIENT_SECRET",
        "KOR_TRAVEL_MAP_GOOGLE_PLACES_API_KEY",
    }
)
_MANAGER_ONLY_CREDENTIAL_NAMES = frozenset(
    {
        "KTDM_C6C_CONTRACT_GENERATION",
        _MAP_UI_PASSWORD_ENV,
        "KTDM_C6C_PINVI_ADMIN_EMAIL",
        _PINVI_ADMIN_PASSWORD_ENV,
    }
)
_SAFE_GET_READINESS_ATTEMPTS = 2
_T = TypeVar("_T")
# 읽기 전용 docker/git 조회 주입점. Docker 없는 단위 검증이 argv를 그대로 관측한다.
C6cCommandRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]
_MAP_UI_AUTH_ENV_NAMES = frozenset(
    {
        _MAP_UI_USERNAME_ENV,
        _MAP_UI_PASSWORD_HASH_ENV,
        _MAP_UI_SESSION_SECRET_ENV,
    }
)
_CONCIERGE_UI_CANONICAL_RAW_ENV_VALUES = {
    _CONCIERGE_UI_BACKEND_ORIGIN_ENV: _CONCIERGE_FIXED_BACKEND_ORIGIN,
    _CONCIERGE_UI_BACKEND_API_KEY_ENV: (
        "${KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY:?"
        "KOR_TRAVEL_CONCIERGE_BACKEND_API_KEY must be explicitly set}"
    ),
    _CONCIERGE_UI_VWORLD_KEY_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY:?"
        "KOR_TRAVEL_CONCIERGE_UI_VWORLD_SERVICE_KEY must be explicitly set}"
    ),
    _CONCIERGE_UI_ADMIN_USERNAME_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME:?"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_USERNAME must be explicitly set}"
    ),
    _CONCIERGE_UI_ADMIN_PASSWORD_HASH_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH:?"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PASSWORD_HASH must be explicitly set}"
    ),
    _CONCIERGE_UI_SESSION_SECRET_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET:?"
        "KOR_TRAVEL_CONCIERGE_UI_SESSION_SECRET must be explicitly set}"
    ),
    _CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET:?"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET must be explicitly set}"
    ),
    _CONCIERGE_UI_TRUST_FORWARDED_IPS_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_TRUST_FORWARDED_IPS:-false}"
    ),
    _CONCIERGE_UI_PUBLIC_ORIGINS_ENV: "${KOR_TRAVEL_CONCIERGE_UI_PUBLIC_ORIGINS:-}",
    _CONCIERGE_UI_PUBLIC_API_BASE_ENV: "${KOR_TRAVEL_CONCIERGE_UI_PUBLIC_API_BASE_URL:-}",
}
_CONCIERGE_API_CANONICAL_RAW_ENV_VALUES = {
    _CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV: (
        "${KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET:?"
        "KOR_TRAVEL_CONCIERGE_UI_ADMIN_PROXY_SECRET must be explicitly set}"
    ),
    "APP_ENV": "${KOR_TRAVEL_CONCIERGE_APP_ENV:-local}",
    "API_AUTH_ENABLED": "${KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED:-false}",
    "API_KEYS": "${KOR_TRAVEL_CONCIERGE_API_KEYS:-}",
}
_CONCIERGE_UI_ENV_SOURCES = {
    _CONCIERGE_UI_BACKEND_API_KEY_ENV: _CONCIERGE_ROOT_BACKEND_API_KEY_ENV,
    _CONCIERGE_UI_VWORLD_KEY_ENV: _CONCIERGE_ROOT_VWORLD_KEY_ENV,
    _CONCIERGE_UI_ADMIN_USERNAME_ENV: _CONCIERGE_ROOT_ADMIN_USERNAME_ENV,
    _CONCIERGE_UI_ADMIN_PASSWORD_HASH_ENV: _CONCIERGE_ROOT_ADMIN_PASSWORD_HASH_ENV,
    _CONCIERGE_UI_SESSION_SECRET_ENV: _CONCIERGE_ROOT_SESSION_SECRET_ENV,
    _CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV: _CONCIERGE_ROOT_PROXY_SECRET_ENV,
    _CONCIERGE_UI_TRUST_FORWARDED_IPS_ENV: _CONCIERGE_ROOT_TRUST_FORWARDED_IPS_ENV,
    _CONCIERGE_UI_PUBLIC_ORIGINS_ENV: _CONCIERGE_ROOT_PUBLIC_ORIGINS_ENV,
    _CONCIERGE_UI_PUBLIC_API_BASE_ENV: _CONCIERGE_ROOT_PUBLIC_API_BASE_ENV,
}
_CONCIERGE_UI_REQUIRED_ROOT_ENV_NAMES = frozenset(
    {
        _CONCIERGE_ROOT_BACKEND_API_KEY_ENV,
        _CONCIERGE_ROOT_VWORLD_KEY_ENV,
        _CONCIERGE_ROOT_ADMIN_USERNAME_ENV,
        _CONCIERGE_ROOT_ADMIN_PASSWORD_HASH_ENV,
        _CONCIERGE_ROOT_SESSION_SECRET_ENV,
        _CONCIERGE_ROOT_PROXY_SECRET_ENV,
    }
)
_MAP_PRODUCTION_SECRET_ENV_NAMES = frozenset(
    {
        _MAP_ADMIN_PROXY_ENV,
        _MAP_SERVICE_TOKEN_ENV,
        _MAP_CURSOR_SIGNING_SECRET_ENV,
        _MAP_GEO_API_KEY_SOURCE_ENV,
        _MAP_UI_GEO_API_KEY_ENV,
    }
)
_MAP_FEATURE_CREATE_ENV_NAMES = frozenset(
    {
        _MAP_FEATURE_CREATE_TOKEN_ENV,
        _MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV,
    }
)
_MAP_FEATURE_CREATE_CONTROL_ENV_NAMES = frozenset(
    {*_MAP_FEATURE_CREATE_ENV_NAMES, _MAP_FEATURE_CREATE_ENABLED_ENV}
)
_CURATION_PRINCIPAL_RAW_ENV_NAMES = frozenset(
    {
        _PINVI_CURATION_SNAPSHOT_ENV,
        _PINVI_CUTOVER_MAPPING_ENV,
    }
)
_CURATION_PRINCIPAL_DIGEST_ENV_NAMES = frozenset(
    {
        _MAP_CURATION_SNAPSHOT_DIGEST_ENV,
        _MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV,
    }
)
_MAP_PUBLISHED_EXAMPLE_SECRET_VALUES = {
    _MAP_ADMIN_PROXY_ENV: "local-map-admin-proxy-secret-change-me",
    _MAP_SERVICE_TOKEN_ENV: "local-map-service-token-change-me-now",
    _MAP_CURSOR_SIGNING_SECRET_ENV: "local-map-cursor-signing-secret-change-me",
}
_MAP_PRODUCTION_API_LITERAL_VALUES = {
    _MAP_PROFILE_ENV: "production",
    _MAP_PUBLIC_API_KEY_REQUIRED_ENV: "true",
    _MAP_DEBUG_ROUTES_ENABLED_ENV: "false",
    _MAP_FEATURES_ROUTES_ENABLED_ENV: "true",
    _MAP_DESTRUCTIVE_ENABLED_ENV: "true",
    _MAP_PROMETHEUS_METRICS_ENABLED_ENV: "false",
    _MAP_ADMIN_TRUSTED_PROXY_CIDRS_ENV: '["127.0.0.1/32","::1/128"]',
}
_MAP_PRODUCTION_API_LITERAL_ENV_NAMES = frozenset(
    _MAP_PRODUCTION_API_LITERAL_VALUES
)
def _candidate_protected_service_order() -> tuple[str, ...]:
    """후보 계약이 서비스별로 보는 필수 서비스(순서). Map Dagster 자리는 스위치를 따른다."""

    return (
        _MAP_API_SERVICE,
        *_map_dagster_runtime_services(),
        _MAP_DAGSTER_STORAGE_MIGRATE_SERVICE,
        _MAP_DB_ROLE_BOOTSTRAP_SERVICE,
        _MAP_APPLICATION_SCHEMA_SERVICE,
        _PINVI_API_SERVICE,
        _PINVI_ADMIN_BOOTSTRAP_SERVICE,
        _MAP_UI_SERVICE,
    )


def _map_database_host_network_services() -> frozenset[str]:
    return frozenset(
        {
            _MAP_API_SERVICE,
            *_map_dagster_runtime_services(),
            _MAP_DAGSTER_STORAGE_MIGRATE_SERVICE,
            _MAP_DB_ROLE_BOOTSTRAP_SERVICE,
            _MAP_APPLICATION_SCHEMA_SERVICE,
        }
    )


_CANDIDATE_REQUIRED_PROTECTED_SERVICES: LazySet[str] | frozenset[str] = LazySet(
    lambda: frozenset(_candidate_protected_service_order())
)
#: 거부 문구가 **이름을 말해도 되는** 서비스. 여기 없으면 sha8로 가려지는데,
#: 그러면 운영자가 어느 서비스가 거부됐는지 알 수 없다 — 적대 리뷰 2026-09-18 C-F6
#: 실측: 이 수정의 **대상 서비스 둘 다**가 가려졌다. 저장소 공개 이름이라 유출
#: 위험이 없고, 정본 compose에 실재하는 것만 넣는다.
_CANDIDATE_NAMEABLE_SERVICE_NAMES: Final = frozenset(
    {
        "kor-travel-shared-postgres",
        "kor-travel-shared-db-init-pinvi",
        "kor-travel-shared-db-init-dagster",
        "kor-travel-dagster-storage-migrate",
    }
)
_CANDIDATE_KNOWN_SERVICE_NAMES: LazySet[str] | frozenset[str] = LazySet(
    lambda: frozenset(_CANDIDATE_REQUIRED_PROTECTED_SERVICES) | _CANDIDATE_NAMEABLE_SERVICE_NAMES
)


def _describe_candidate_service_key(service_name: object) -> str:
    """계약 오류 문구에 실을 서비스 키의 표현.

    이 문구는 CLI stderr(`cli.py`의 `DeploymentContractError` 핸들러)와 HTTP 409/500
    body(`main.py`의 예외 핸들러, `api/routes.py::_config_failure_detail`)로 나간다.
    그런데 서비스 키는 **후보 문서 작성자가 정하는 임의 문자열**이라, 보호값이 키
    자리에 들어오면 그대로 에코된다 — 적대 리뷰 2026-09-17이
    `KOR_TRAVEL_MAP_POSTGRES_PASSWORD` 값을 서비스 이름으로 넣어 실측했다(main은
    싣지 않았고 S1의 첫 판만 실었다). 게다가 이 루프는 보호 이름/값 전역 스캔보다
    **앞**이라 그 스캔이 막아 주지도 못한다.

    권한 상승은 아니다 — 그 키는 운영자가 직접 적은 것이다. 심층 방어이고, 이
    저장소가 이미 명시적으로 지키는 계약이다(`cli.py`: "원문은 여전히 JSON에 넣지
    않는다"). 그래서 **계약이 아는 이름일 때만 그대로 지목하고**, 모르는 키는
    sha8로만 가리킨다. 운영자는 자기 키를 해싱해 대조할 수 있고 우리는 아무것도
    흘리지 않는다.

    S4가 required 집합을 좁히면 아는 이름이 줄어 해싱되는 키가 늘어난다 — 완화가
    노출을 **넓히지 않는** 방향이라 그대로 두어도 안전하다.
    """

    if isinstance(service_name, str) and service_name in _CANDIDATE_KNOWN_SERVICE_NAMES:
        return service_name
    digest = hashlib.sha256(repr(service_name).encode("utf-8")).hexdigest()[:8]
    return f"<unrecognized service key sha256:{digest}>"


_OPS_ENV_NAMES = frozenset(
    {
        _MAP_READ_ENV,
        _MAP_CANCEL_ENV,
        _MAP_FIXTURE_ENV,
        _MAP_REQUIRED_ENV,
        _PINVI_READ_ENV,
        _PINVI_CANCEL_ENV,
    }
)
#: PinVi DSN을 조립하는 서비스와, 그때 쓰는 자격증명 쌍.
#:
#: canonical 값은 이 선언에서 유도한다. 보호 참조가 놓일 수 있는 자리는 표가 아니라 설치된 릴리스
#: compose에서 파생한다(ADR-51 결정 5) — 서비스를 더해도 등록할 곳이 없다.
_PINVI_DSN_SERVICE_CREDENTIALS: Final[LazySequence[tuple[str, str, str]]] = LazySequence(
    lambda: tuple(
        (service_name, _PINVI_APP_DB_USER_ENV, _PINVI_APP_DB_PASSWORD_ENV)
        for service_name in (
            _PINVI_API_SERVICE,
            *_pinvi_dagster().processes,
            _PINVI_ADMIN_BOOTSTRAP_SERVICE,
        )
    )
)


def _pinvi_dsn(*, scheme: str, username_env: str, password_env: str, database: str) -> str:
    """compose가 적는 **raw**(미해석) DSN 문자열. 계약은 이 글자열을 고정한다.

    ADR-46 이전에는 `${PINVI_DB_PORT:-12800}`(전용 instance)이었다. 앱/Dagster DSN은
    공용 instance로 옮겼으므로 `KOR_TRAVEL_SHARED_DB_PORT`를 쓴다.
    """

    return (
        f"{scheme}://"
        f"${{{username_env}:?{username_env} must be explicitly set}}:"
        f"${{{password_env}:?{password_env} must be explicitly set}}"
        f"@127.0.0.1:${{KOR_TRAVEL_SHARED_DB_PORT:-{_PINVI_SHARED_POSTGRES_PORT}}}/{database}"
    )


@lru_cache(maxsize=1)
def _build_pinvi_database_url_raw_values() -> dict[str, str]:
    return {
        service_name: _pinvi_dsn(
            scheme="postgresql+asyncpg",
            username_env=username_env,
            password_env=password_env,
            database="${PINVI_POSTGRES_DB:-pinvi}",
        )
        for service_name, username_env, password_env in _PINVI_DSN_SERVICE_CREDENTIALS
    }


_PINVI_DATABASE_URL_RAW_VALUES: Final[LazyMapping[str, str]] = LazyMapping(
    _build_pinvi_database_url_raw_values
)

#: Dagster instance storage DSN. webserver·code-server·daemon이 **같은 storage**를
#: 봐야 하므로 셋 다 같은 값을 든다. 앱 DSN과 달리 `postgresql://`(동기)이고
#: 데이터베이스가 `pinvi_dagster`다 — 저장소를 앱 DB와 가르는 것이 #356의 요지다.
_PINVI_DAGSTER_PG_URL_SERVICES: Final[LazySequence[str]] = LazySequence(
    lambda: _pinvi_dagster().processes
)


@lru_cache(maxsize=1)
def _build_pinvi_dagster_pg_url_raw_values() -> dict[str, str]:
    return {
        service_name: _pinvi_dsn(
            scheme="postgresql",
            username_env=_PINVI_APP_DB_USER_ENV,
            password_env=_PINVI_APP_DB_PASSWORD_ENV,
            database="${PINVI_DAGSTER_DB:-pinvi_dagster}",
        )
        for service_name in _PINVI_DAGSTER_PG_URL_SERVICES
    }


_PINVI_DAGSTER_PG_URL_RAW_VALUES: Final[LazyMapping[str, str]] = LazyMapping(
    _build_pinvi_dagster_pg_url_raw_values
)



@lru_cache(maxsize=1)
def _build_map_database_canonical_env_values() -> dict[tuple[str, str], str]:
    return {
        # role bootstrap one-shot에는 bootstrap DSN·instance admin 이름·포트가 없다(ADR-53 S1) —
        # 이름·포트는 Manager가 실행 시점 `-e`로, password는 instance의 secret file로 준다.
        # 그 셋이 compose env에 없다는 것은 `_validate_map_db_role_bootstrap_service`가 본다.
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_DB_ROLE_BOOTSTRAP_CONFIRM_DATABASE"): (
            "${KOR_TRAVEL_MAP_POSTGRES_DB:?"
            "KOR_TRAVEL_MAP_POSTGRES_DB must be explicitly set}"
        ),
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_POSTGRES_DB"): (
            "${KOR_TRAVEL_MAP_POSTGRES_DB:?"
            "KOR_TRAVEL_MAP_POSTGRES_DB must be explicitly set}"
        ),
        # ADR-100: Map의 세 LOGIN이 ktm_feature_service 하나로 합쳐졌다. 구 여섯 이름(MIGRATOR·
        # API_RUNTIME·DAGSTER_RUNTIME의 DSN·password)을 함께 보내던 superset 창은 D10으로 닫았다.
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_SERVICE_PASSWORD"): (
            "${KOR_TRAVEL_MAP_SERVICE_PASSWORD:?"
            "KOR_TRAVEL_MAP_SERVICE_PASSWORD must be explicitly set}"
        ),
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_PG_DSN"): (
            "${KOR_TRAVEL_MAP_PG_DSN:?"
            "KOR_TRAVEL_MAP_PG_DSN must be explicitly set}"
        ),
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB"): (
            "${KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB:?"
            "KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB must be explicitly set}"
        ),
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER"): (
            "${KOR_TRAVEL_MAP_DAGSTER_METADATA_USER:?"
            "KOR_TRAVEL_MAP_DAGSTER_METADATA_USER must be explicitly set}"
        ),
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD"): (
            "${KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD:?"
            "KOR_TRAVEL_MAP_DAGSTER_METADATA_PASSWORD must be explicitly set}"
        ),
        (_MAP_DB_ROLE_BOOTSTRAP_SERVICE, "KOR_TRAVEL_MAP_DAGSTER_PG_URL"): (
            "${KOR_TRAVEL_MAP_DAGSTER_PG_URL:?"
            "KOR_TRAVEL_MAP_DAGSTER_PG_URL must be explicitly set}"
        ),
        (_MAP_API_SERVICE, "KOR_TRAVEL_MAP_PG_DSN"): (
            "${KOR_TRAVEL_MAP_PG_DSN:?"
            "KOR_TRAVEL_MAP_PG_DSN must be explicitly set}"
        ),
        **{
            (service, "KOR_TRAVEL_MAP_DAGSTER_PG_URL"): (
                "${KOR_TRAVEL_MAP_DAGSTER_PG_URL:?"
                "KOR_TRAVEL_MAP_DAGSTER_PG_URL must be explicitly set}"
            )
            for service in (
                *_map_dagster().processes,
                _MAP_DAGSTER_STORAGE_MIGRATE_SERVICE,
            )
        },
        **{
            (service, "KOR_TRAVEL_MAP_PG_DSN"): (
                "${KOR_TRAVEL_MAP_PG_DSN:?"
                "KOR_TRAVEL_MAP_PG_DSN must be explicitly set}"
            )
            for service in _map_dagster().processes
        },
        (_MAP_APPLICATION_SCHEMA_SERVICE, "KOR_TRAVEL_MAP_PG_DSN"): (
            "${KOR_TRAVEL_MAP_PG_DSN:?"
            "KOR_TRAVEL_MAP_PG_DSN must be explicitly set}"
        ),
        (
            _MAP_APPLICATION_SCHEMA_SERVICE,
            "KOR_TRAVEL_MAP_ALEMBIC_USE_SCHEMA_OWNER_ROLE",
        ): "true",
    }


_MAP_DATABASE_CANONICAL_ENV_VALUES: Final[Mapping[Any, Any]] = LazyMapping(_build_map_database_canonical_env_values)
@lru_cache(maxsize=1)
def _build_candidate_canonical_api_env_values() -> dict[tuple[str, str], str]:
    return {
        (_MAP_API_SERVICE, _MAP_READ_ENV): "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}",
        (_MAP_API_SERVICE, _MAP_CANCEL_ENV): "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}",
        (_MAP_API_SERVICE, _MAP_FIXTURE_ENV): "${KOR_TRAVEL_MAP_API_OPS_FIXTURE_TOKEN:-}",
        (_MAP_API_SERVICE, _MAP_REQUIRED_ENV): (
            "${KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED:?"
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED must be explicitly set}"
        ),
        (_PINVI_API_SERVICE, _PINVI_READ_ENV): "${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}",
        (_PINVI_API_SERVICE, _PINVI_CANCEL_ENV): ("${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"),
        (_PINVI_ADMIN_BOOTSTRAP_SERVICE, _PINVI_READ_ENV): ("${KOR_TRAVEL_MAP_API_OPS_READ_TOKEN:-}"),
        (_PINVI_ADMIN_BOOTSTRAP_SERVICE, _PINVI_CANCEL_ENV): (
            "${KOR_TRAVEL_MAP_API_OPS_CANCEL_TOKEN:-}"
        ),
        (_MAP_UI_SERVICE, _MAP_UI_USERNAME_ENV): (
            "${KOR_TRAVEL_MAP_UI_ADMIN_USERNAME:?"
            "KOR_TRAVEL_MAP_UI_ADMIN_USERNAME must be explicitly set}"
        ),
        (_MAP_UI_SERVICE, _MAP_UI_PASSWORD_HASH_ENV): (
            "${KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH:?"
            "KOR_TRAVEL_MAP_UI_ADMIN_PASSWORD_HASH must be explicitly set}"
        ),
        (_MAP_UI_SERVICE, _MAP_UI_SESSION_SECRET_ENV): (
            "${KOR_TRAVEL_MAP_UI_SESSION_SECRET:?"
            "KOR_TRAVEL_MAP_UI_SESSION_SECRET must be explicitly set}"
        ),
        (_MAP_API_SERVICE, _MAP_ADMIN_PROXY_ENV): (
            "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET must be explicitly set}"
        ),
        (_MAP_UI_SERVICE, _MAP_ADMIN_PROXY_ENV): (
            "${KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET:?"
            "KOR_TRAVEL_MAP_ADMIN_PROXY_SECRET must be explicitly set}"
        ),
        (_MAP_API_SERVICE, _MAP_SERVICE_TOKEN_ENV): (
            "${KOR_TRAVEL_MAP_API_SERVICE_TOKEN:?"
            "KOR_TRAVEL_MAP_API_SERVICE_TOKEN must be explicitly set}"
        ),
        (_MAP_API_SERVICE, _MAP_CURSOR_SIGNING_SECRET_ENV): (
            "${KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET:?"
            "KOR_TRAVEL_MAP_API_CURSOR_SIGNING_SECRET must be explicitly set}"
        ),
        (_MAP_API_SERVICE, _MAP_GEO_API_KEY_SOURCE_ENV): ("${KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY}"),
        (_MAP_UI_SERVICE, _MAP_UI_GEO_API_KEY_ENV): (
            "${KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY:?"
            "KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY must be explicitly set}"
        ),
        **{
            (service, _MAP_GEO_API_KEY_SOURCE_ENV): "${KOR_TRAVEL_MAP_KOR_TRAVEL_GEO_API_KEY}"
            for service in _map_dagster().processes
        },
        (_MAP_API_SERVICE, _MAP_CURATION_SNAPSHOT_DIGEST_ENV): (
            "${KOR_TRAVEL_MAP_API_PINVI_CURATION_SNAPSHOT_TOKEN_SHA256:-}"
        ),
        (_MAP_API_SERVICE, _MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV): (
            "${KOR_TRAVEL_MAP_API_PINVI_CURATION_CUTOVER_MAPPING_TOKEN_SHA256:-}"
        ),
        (_MAP_API_SERVICE, _MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV): (
            "${KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256:?"
            "KOR_TRAVEL_MAP_API_ADMIN_FEATURE_CREATE_TOKEN_SHA256 must be explicitly set}"
        ),
        (_MAP_API_SERVICE, _MAP_FEATURE_CREATE_ENABLED_ENV): (
            "${KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED:-false}"
        ),
        (_MAP_UI_SERVICE, _MAP_FEATURE_CREATE_TOKEN_ENV): (
            "${KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN:?"
            "KOR_TRAVEL_MAP_ADMIN_FEATURE_CREATE_TOKEN must be explicitly set}"
        ),
        (_PINVI_API_SERVICE, _PINVI_CURATION_SNAPSHOT_ENV): (
            "${PINVI_KOR_TRAVEL_MAP_CURATION_SNAPSHOT_TOKEN:-}"
        ),
        (_PINVI_API_SERVICE, _PINVI_CUTOVER_MAPPING_ENV): (
            "${PINVI_KOR_TRAVEL_MAP_CURATION_CUTOVER_MAPPING_TOKEN:-}"
        ),
        **{
            (_MAP_API_SERVICE, env_name): value
            for env_name, value in _MAP_PRODUCTION_API_LITERAL_VALUES.items()
        },
        **_MAP_DATABASE_CANONICAL_ENV_VALUES,
    }


_CANDIDATE_CANONICAL_API_ENV_VALUES: Final[Mapping[Any, Any]] = LazyMapping(_build_candidate_canonical_api_env_values)
# 계약이 값을 고정한 env 이름 — 화면이 처음부터 잠글 수 있도록 service별로 공개한다.
#
# 이 이름들을 UI 설정 편집기가 편집 가능하게 노출하면 저장은 성공하고, 그 다음
# `rebuild-pinned`가 candidate 검증에서 fail-close한다. 실패가 mutation보다 한참 뒤에
# 오므로 원인이 화면 조작이었다는 사실이 드러나지 않고, 그 사이 pinset 하나가 소모된다.
# 이 목록은 candidate 계약 자체(`_CANDIDATE_CANONICAL_API_ENV_VALUES`)에서 유도하므로
# 계약이 바뀌면 화면도 함께 바뀐다 — 손으로 관리하는 두 번째 목록을 만들지 않는다.
@lru_cache(maxsize=1)
def _build_contract_locked_env_names_by_service() -> dict[str, frozenset[str]]:
    locked: dict[str, frozenset[str]] = {
        service_name: frozenset(
            env_name
            for candidate_service, env_name in _CANDIDATE_CANONICAL_API_ENV_VALUES
            if candidate_service == service_name
        )
        for service_name in {service for service, _ in _CANDIDATE_CANONICAL_API_ENV_VALUES}
    }
# DSN은 위 dict가 아니라 별도 검증기가 결박한다(`_PINVI_DATABASE_URL_RAW_VALUES`,
# `_PINVI_DAGSTER_PG_URL_RAW_VALUES`). 계약의
# 소유자가 다르므로 유도하지 않고 명시하되, **덮어쓰지 않고 합집합을 취한다** —
# 대입으로 두면 나중에 같은 service가 candidate 계약에 등장했을 때 유도된 이름들이
# 조용히 사라진다.
    for service_name, extra_locked in (
        *(
            (dsn_service, {_PINVI_DATABASE_URL_ENV})
            for dsn_service in _PINVI_DATABASE_URL_RAW_VALUES
        ),
        *(
            (dsn_service, {_PINVI_DAGSTER_PG_URL_ENV})
            for dsn_service in _PINVI_DAGSTER_PG_URL_RAW_VALUES
        ),
    ):
        locked[service_name] = frozenset(
            locked.get(service_name, frozenset()) | frozenset(extra_locked)
        )
    return locked


_CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE: Final[LazyMapping[str, frozenset[str]]] = LazyMapping(
    _build_contract_locked_env_names_by_service
)

CONTRACT_LOCKED_ENV_REASON: Final = (
    "이 값은 배포 계약이 고정한 값입니다. 여기서 바꾸면 저장은 되지만 다음 재구축이 "
    "거부됩니다."
)


def contract_locked_env_names(service_name: str) -> tuple[str, ...]:
    """이 compose service에서 배포 계약이 값을 고정한 env 이름(정렬)."""

    return tuple(sorted(_CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE.get(service_name, ())))


def assert_contract_locked_env_unchanged(
    *,
    service_name: str,
    env: Mapping[str, Any],
    baseline_env: Mapping[str, Any],
) -> None:
    """계약이 고정한 값의 변경을 **저장 시점에** 거부한다.

    재구축까지 미루면 실패가 조작과 멀어져 원인을 찾기 어렵다. 여기서 막으면
    "무엇을 바꾸려 했는지"가 그대로 오류에 남는다.

    **삭제와 추가도 변경이다.** 저장은 environment 매핑을 통째로 교체하므로, 키를
    빼고 보내는 것이 곧 삭제다. "양쪽에 있을 때만 비교"하면 삭제와 추가가 전부
    통과해 같은 지연 실패가 그대로 남는다 — 없음을 sentinel로 두고 비교한다.
    """

    locked = _CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE.get(service_name)
    if not locked:
        return
    missing = object()

    def _value(mapping: Mapping[str, Any], name: str) -> Any:
        raw = mapping.get(name, missing)
        return raw if raw is missing else str(raw)

    changed = sorted(
        name for name in locked if _value(env, name) != _value(baseline_env, name)
    )
    if changed:
        raise ComposeCandidateContractError(
            f"배포 계약이 고정한 환경변수는 이 화면에서 바꿀 수 없습니다"
            f"(삭제·추가 포함): {', '.join(changed)}"
        )




@dataclass(frozen=True)
class _MapDatabaseDsnIdentity:
    """모양 검사를 지난 Map DSN의 비밀 아닌 좌표."""

    port: int
    login: str
    metadata_user: str


def _validate_map_database_dsn_identities(
    environment: Mapping[str, str],
) -> _MapDatabaseDsnIdentity:
    """Map의 두 DSN이 한 authority·정본 모양을 가리키는지 확인한다(ADR-53). raw·resolved 공통.

    - `KOR_TRAVEL_MAP_PG_DSN`: `postgresql+asyncpg`, host `127.0.0.1`, path `/<앱 DB>`.
    - `KOR_TRAVEL_MAP_DAGSTER_PG_URL`: `postgresql`, host `127.0.0.1`, user = metadata user,
      path `/<Dagster DB>`.
    - 둘은 같은 포트다. 그 포트가 어느 서버의 것인지는 resolved 문서만 안다
      (`_validate_map_database_dsn_instance`) — raw는 `-p`를 보간할 수 없으므로 모양만 본다.
    - metadata user는 Map principal(`ktm_*`)이 아니고 Dagster DB 이름과 같다(Map bootstrap 규칙의
      거울, M1 b2). 앱 DB와 Dagster DB는 이름이 다르다.

    bootstrap DSN은 env에 없다 — 그것은 one-shot 안에서 실행 시점에 만든다(S1). ADR-100의 세
    DSN(MIGRATOR·API_RUNTIME·DAGSTER_RUNTIME)도 창을 닫았다(D10). credential은 비교하거나 오류에
    넣지 않는다.
    """

    application_database = environment.get("KOR_TRAVEL_MAP_POSTGRES_DB", "")
    dagster_database = environment.get("KOR_TRAVEL_MAP_DAGSTER_POSTGRES_DB", "")
    metadata_user = environment.get("KOR_TRAVEL_MAP_DAGSTER_METADATA_USER", "")
    if (
        not metadata_user
        or metadata_user.startswith(MAP_PRINCIPAL_PREFIX)
        or not application_database
        or not dagster_database
        or application_database == dagster_database
    ):
        raise ComposeCandidateContractError("Map database DSN identity is invalid")
    if metadata_user != dagster_database:
        # Map bootstrap one-shot의 규칙(`database-credential-preflight.sh`)을 비춘다. 그쪽은
        # 재구축의 DB 초기화 **뒤에** 돌므로, 어긋난 쌍은 여기서 어떤 단계보다 먼저 거부한다.
        raise ComposeCandidateContractError(
            "Map Dagster metadata user must equal the Dagster database name"
        )
    application = _parse_map_database_dsn(
        environment,
        "KOR_TRAVEL_MAP_PG_DSN",
        scheme="postgresql+asyncpg",
        database=application_database,
    )
    dagster = _parse_map_database_dsn(
        environment,
        "KOR_TRAVEL_MAP_DAGSTER_PG_URL",
        scheme="postgresql",
        database=dagster_database,
    )
    if application.port != dagster.port:
        raise ComposeCandidateContractError(
            "Map database DSNs must share one PostgreSQL instance"
        )
    if unquote(dagster.username or "") != metadata_user:
        raise ComposeCandidateContractError("Map database DSN identity is invalid")
    # login은 이름으로 고정하지 않는다(Map이 소유한다, M1). 그것이 정말 Map의 login인지는 R4가
    # live role 그래프로 확인한다 — 여기서는 metadata user 자리를, resolved 경로에서는 instance
    # admin 자리도 막는다.
    login = unquote(application.username or "")
    if not login or login == metadata_user:
        raise _map_login_error()
    return _MapDatabaseDsnIdentity(
        port=cast(int, application.port), login=login, metadata_user=metadata_user
    )


def _map_login_error() -> ComposeCandidateContractError:
    return ComposeCandidateContractError(
        "Map application login must be a service login outside the instance admin "
        "and the Dagster metadata role"
    )


def _validate_map_database_dsn_instance(
    environment: Mapping[str, str],
    *,
    resolved: Mapping[str, Any],
    identity: _MapDatabaseDsnIdentity | None = None,
) -> None:
    """resolved 문서에서 Map DSN 포트가 가리키는 instance를 유도하고 그 admin을 비춘다(ADR-53).

    그 포트를 `-p`로 듣는 PostgreSQL 서버 서비스가 **정확히 하나**여야 한다 — instance는 이름이
    아니라 포트에서 온다. 퇴역 instance의 포트가 `.env`에 남으면(0개) 또는 두 서버가 같은 포트를
    말하면(2개) 거부한다. login과 metadata user는 그 instance의 admin(`POSTGRES_USER`)이 아니다.

    서버 형태 술어(`_assert_postgres_cluster_runtime_is_canonical`) **뒤에** 부른다 — 서버가
    무엇인지 먼저 확정한 뒤에 포트로 고른다. 호출자가 같은 env로 이미 모양을 검사했으면 그
    ``identity``를 넘긴다(한 번만 판정한다).
    """

    if identity is None:
        identity = _validate_map_database_dsn_identities(environment)
    instances = postgres_server_services_on_port(resolved, identity.port)
    if len(instances) != 1:
        raise ComposeCandidateContractError(
            "Map database DSN port must be the -p of exactly one PostgreSQL server "
            f"service (found {len(instances)})"
        )
    admin = postgres_server_admin_name(resolved, instances[0])
    if admin is None or identity.metadata_user == admin:
        raise ComposeCandidateContractError("Map database DSN identity is invalid")
    if identity.login == admin:
        raise _map_login_error()


#: Map이 cluster 전역에 두는 role 가족의 접두(Map `docker/postgres-role-bootstrap.sh`의
#: reserved inventory). Dagster metadata login은 그 가족이 아니다. database_runtime의 S1 판정도
#: 이것에서 LIKE 패턴을 만든다.
MAP_PRINCIPAL_PREFIX: Final = "ktm_"

#: Manager가 다루는 PostgreSQL role·database 이름의 모양. instance admin 이름을 읽는 C6c와
#: database_runtime이 이것 하나를 쓴다 — 두 술어가 다르면 C6c가 받은 이름을 재구축이 나중에
#: 거부한다.
POSTGRES_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def loopback_dsn_authority(dsn: object) -> tuple[str, int] | None:
    """DSN의 `(host, port)` — host가 `127.0.0.1`이고 포트가 1..65535일 때만. 아니면 ``None``.

    모든 instance는 loopback만 듣는다. DSN authority를 읽는 자리(C6c의 Map DSN 검사와 role
    bootstrap one-shot, database_runtime의 instance 유도)가 이것 하나를 쓴다.
    """

    try:
        parsed = urlsplit(dsn if isinstance(dsn, str) else "")
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if host != "127.0.0.1" or port is None or not 1 <= port <= 65535:
        return None
    return host, port


def _parse_map_database_dsn(
    environment: Mapping[str, str],
    name: str,
    *,
    scheme: str,
    database: str,
) -> SplitResult:
    """Map DSN 하나의 scheme·host·port·path를 확인하고 파싱 결과를 낸다(값은 싣지 않는다)."""

    value = environment.get(name, "")
    if loopback_dsn_authority(value) is None:
        raise ComposeCandidateContractError("Map database DSN identity is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != scheme
        or parsed.path != f"/{database}"
        or parsed.query
        or parsed.fragment
    ):
        raise ComposeCandidateContractError("Map database DSN identity is invalid")
    return parsed


def postgres_server_admin_name(
    document: Mapping[str, Any],
    service_name: str,
) -> str | None:
    """PostgreSQL 서버 서비스의 `POSTGRES_USER`(instance admin). 없거나 모양이 틀리면 ``None``.

    C6c의 Map DSN 검사와 database_runtime의 instance 유도가 이것 하나로 admin을 읽는다.
    """

    services = document.get("services")
    service = services.get(service_name) if isinstance(services, Mapping) else None
    if not isinstance(service, Mapping):
        return None
    admin = dict(_service_environment_items(service)).get("POSTGRES_USER")
    if not isinstance(admin, str) or POSTGRES_IDENTIFIER.fullmatch(admin) is None:
        return None
    return admin


@dataclass(frozen=True)
class _PinviDatabaseIdentity:
    """전역 env 불변식이 통과한 뒤 per-service 검사가 쓰는 파생값."""

    expected_port: int
    expected_database: str
    role_values: Mapping[str, str]


def _validate_pinvi_database_url_environment(
    environment: Mapping[str, str],
) -> _PinviDatabaseIdentity:
    """(전역) PinVi DB의 **env 불변식** — 어떤 서비스의 존재와도 무관하다.

    셋을 본다:

    - `KOR_TRAVEL_SHARED_DB_PORT == 11000` 고정(ADR-46 — 앱/Dagster DSN이 실제로
      접속하는 공용 instance 포트)
    - app role 이름과 password가 비어 있지 않다
    - app role 이름의 정규식 `[a-z_][a-z0-9_]*`

    2026-09-28까지는 전용 instance(pinvi-postgres)의 포트(`PINVI_DB_PORT == 12800`)와
    그 superuser(`PINVI_POSTGRES_USER`/`PINVI_POSTGRES_PASSWORD`)까지 함께 묶었다.
    그 instance를 compose에서 뺐으므로 그 셋은 이제 어느 서비스도 읽지 않는다.

    **이 함수에 family 조건을 달지 마라.** 종전에는 이 블록이 per-service 검사와 한
    함수에 있어서, S4가 그 호출을 family scope로 게이팅하면 여기까지 함께 꺼졌다 —
    S3-a가 loopback 결박에 대해 고친 것과 같은 모양이다. Map 쪽 쌍둥이
    (`_validate_map_database_dsn_identities`)는 이미 `environment`만 받는다.

    참고로 같은 술어가 `pinvi_database_role_credentials._validate_credentials`에도
    있다(저장소 유일이 아니다). 다만 그 자리는 `rebuild_pinned_runtime` 경로에만
    있어서, managed compose mutation 경로에서는 이 절이 유일한 그물이다.
    """

    try:
        expected_port = int(
            environment.get("KOR_TRAVEL_SHARED_DB_PORT", str(_PINVI_SHARED_POSTGRES_PORT))
        )
    except (TypeError, ValueError) as exc:
        raise ComposeCandidateContractError("PinVi database URL identity is invalid") from exc
    expected_database = environment.get("PINVI_POSTGRES_DB", "pinvi")
    # M05 폐기: role은 application 하나뿐이다(geo 패턴). 종전에는 schema owner·
    # migration owner·migrator까지 넷을 서로 다른 이름으로 요구했다.
    role_values = {
        _PINVI_APP_DB_USER_ENV: environment.get(_PINVI_APP_DB_USER_ENV),
        _PINVI_APP_DB_PASSWORD_ENV: environment.get(_PINVI_APP_DB_PASSWORD_ENV),
    }
    application_user = role_values[_PINVI_APP_DB_USER_ENV]
    if (
        expected_port != _PINVI_SHARED_POSTGRES_PORT
        or not expected_database
        or any(not isinstance(value, str) or not value for value in role_values.values())
        or not isinstance(application_user, str)
        or re.fullmatch(r"[a-z_][a-z0-9_]*", application_user) is None
    ):
        raise ComposeCandidateContractError("PinVi database URL identity is invalid")

    return _PinviDatabaseIdentity(
        expected_port=expected_port,
        expected_database=cast(str, expected_database),
        role_values=cast("Mapping[str, str]", role_values),
    )


def _validate_pinvi_database_url_service_identities(
    services: Mapping[str, Any],
    identity: _PinviDatabaseIdentity,
    *,
    resolved: bool,
) -> None:
    """(per-service) 세 서비스의 DSN이 role 분리를 지키는가.

    `services`를 보는 절반이고, 이미 존재-조건부다(`.get()` + `continue`) — 그래서
    S4가 게이팅할 수 있는 쪽이다. 전역 불변식은
    `_validate_pinvi_database_url_environment`가 따로 본다.
    """

    expected_port = identity.expected_port
    expected_database = identity.expected_database
    role_values = identity.role_values
    # **서비스 목록을 다시 적지 않는다.** 어느 서비스가 어느 자격증명으로 DSN을
    # 조립하는지는 `_PINVI_DSN_SERVICE_CREDENTIALS` 하나가 소유한다 — 사본을 두면
    # 서비스가 늘 때 한쪽만 자라고, 그 어긋남은 n150 재구축 시점에만 드러난다.
    expected_credentials = {
        service_name: (
            cast(str, role_values[username_env]),
            cast(str, role_values[password_env]),
        )
        for service_name, username_env, password_env in _PINVI_DSN_SERVICE_CREDENTIALS
    }
    for service_name, (expected_user, expected_password) in expected_credentials.items():
        service = services.get(service_name)
        if not isinstance(service, Mapping):
            continue
        service_environment = service.get("environment")
        if not isinstance(service_environment, Mapping):
            raise ComposeCandidateContractError("PinVi database URL identity is invalid")
        value = service_environment.get(_PINVI_DATABASE_URL_ENV)
        if not isinstance(value, str) or not value:
            raise ComposeCandidateContractError("PinVi database URL identity is invalid")
        if not resolved:
            if not hmac.compare_digest(value, _PINVI_DATABASE_URL_RAW_VALUES[service_name]):
                raise ComposeCandidateContractError("PinVi database URL identity is invalid")
            continue
        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError as exc:
            raise ComposeCandidateContractError("PinVi database URL identity is invalid") from exc
        if (
            parsed.scheme not in {"postgresql", "postgresql+asyncpg"}
            or parsed.hostname != "127.0.0.1"
            or parsed_port != expected_port
            or unquote(parsed.username or "") != expected_user
            or not hmac.compare_digest(unquote(parsed.password or ""), expected_password)
            or parsed.path != f"/{expected_database}"
            or parsed.query
            or parsed.fragment
        ):
            raise ComposeCandidateContractError("PinVi database URL identity is invalid")

    # Dagster instance storage DSN. webserver·code-server·daemon이 **같은** storage를
    # 봐야 하고(다르면 schedule 켜짐 상태와 run 이력이 갈린다), 그 storage는 앱 DB와
    # **달라야 한다**(#356의 요지 — 적재 트랜잭션과 Dagster 쓰기를 가른다).
    for service_name in _PINVI_DAGSTER_PG_URL_SERVICES:
        service = services.get(service_name)
        if not isinstance(service, Mapping):
            continue
        service_environment = service.get("environment")
        if not isinstance(service_environment, Mapping):
            raise ComposeCandidateContractError("PinVi Dagster storage URL is invalid")
        value = service_environment.get(_PINVI_DAGSTER_PG_URL_ENV)
        if not isinstance(value, str) or not value:
            raise ComposeCandidateContractError("PinVi Dagster storage URL is invalid")
        if not resolved:
            if not hmac.compare_digest(
                value, _PINVI_DAGSTER_PG_URL_RAW_VALUES[service_name]
            ):
                raise ComposeCandidateContractError(
                    "PinVi Dagster storage URL is invalid"
                )
            continue
        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError as exc:
            raise ComposeCandidateContractError(
                "PinVi Dagster storage URL is invalid"
            ) from exc
        expected_user = cast(str, role_values[_PINVI_APP_DB_USER_ENV])
        expected_password = cast(str, role_values[_PINVI_APP_DB_PASSWORD_ENV])
        if (
            parsed.scheme != "postgresql"
            or parsed.hostname != "127.0.0.1"
            or parsed_port != expected_port
            or unquote(parsed.username or "") != expected_user
            or not hmac.compare_digest(unquote(parsed.password or ""), expected_password)
            or not parsed.path.lstrip("/")
            # 앱 DB와 같은 이름이면 #356이 가른 것이 도로 붙은 것이다.
            or parsed.path == f"/{expected_database}"
            or parsed.query
            or parsed.fragment
        ):
            raise ComposeCandidateContractError("PinVi Dagster storage URL is invalid")


def _require_map_database_host_network(service: Mapping[str, Any]) -> None:
    if service.get("network_mode") != "host":
        raise ComposeCandidateContractError(
            "resolved Map database runtime must use host network"
        )


def _validate_map_application_300_images(
    services: Mapping[str, Any],
) -> None:
    """schema one-shot이 Map API와 **같은** candidate image를 쓰는지 고정한다.

    ADR-101 이전에는 두 one-shot을 각각 대조했다. 하나가 된 지금도 성질은 같다 —
    스키마를 만드는 코드와 그 스키마로 도는 코드가 같은 이미지에서 나와야 한다.
    """

    map_api = services.get(_MAP_API_SERVICE)
    schema = services.get(_MAP_APPLICATION_SCHEMA_SERVICE)
    if not isinstance(map_api, Mapping) or not isinstance(schema, Mapping):
        raise ComposeCandidateContractError(
            "Map application 300 image provenance is invalid"
        )
    map_image = map_api.get("image")
    if (
        not isinstance(map_image, str)
        or not map_image
        or schema.get("image") != map_image
    ):
        raise ComposeCandidateContractError(
            "Map application 300 image provenance is invalid"
        )


def _validate_map_application_300_service(
    service_name: str,
    service: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
    resolved: bool,
) -> None:
    """application schema one-shot의 최소 권한 실행 표면을 고정한다.

    ADR-101 이전에는 두 service였고(root migration / finalize), 각각 Map 이미지의
    전용 실행파일을 고정된 operation과 writer-fence 영수증 경로로 불렀다. 그 실행파일
    둘은 Map 이미지에서 삭제됐고, operation 인자와 fence 영수증은 그 CLI의 일부였다.

    지키는 성질은 그대로다 — 이 one-shot은 **고정된 형상으로만** 돈다. environment는
    정확히 네 키이고, entrypoint는 고정된 두 명령이며, 한 번 돌고 끝난다.

    `KOR_TRAVEL_MAP_ALEMBIC_USE_SCHEMA_OWNER_ROLE`가 여기 **있다는 것**과 런타임
    서비스에 **없다는 것**이 함께 계약이다. migration만 schema owner로 돌아야 한다.
    """

    if service_name != _MAP_APPLICATION_SCHEMA_SERVICE:
        raise ComposeCandidateContractError(
            "Map application 300 service identity is invalid"
        )

    if resolved:
        image_id = environment.get("KOR_TRAVEL_MAP_API_IMAGE", "")
        service_dsn = environment.get("KOR_TRAVEL_MAP_PG_DSN", "")
    else:
        image_id = (
            "${KOR_TRAVEL_MAP_API_IMAGE:?"
            "KOR_TRAVEL_MAP_API_IMAGE must be explicitly set}"
        )
        service_dsn = _MAP_DATABASE_CANONICAL_ENV_VALUES[
            (service_name, "KOR_TRAVEL_MAP_PG_DSN")
        ]
    expected_environment = {
        "KOR_TRAVEL_MAP_APPLICATION_SCHEMA_PROFILE": "production",
        "KOR_TRAVEL_MAP_APPLICATION_SCHEMA_IMAGE_ID": image_id,
        "KOR_TRAVEL_MAP_PG_DSN": service_dsn,
        "KOR_TRAVEL_MAP_ALEMBIC_USE_SCHEMA_OWNER_ROLE": "true",
    }
    if service.get("environment") != expected_environment:
        raise ComposeCandidateContractError(
            "Map application 300 service environment is invalid"
        )
    if service.get("entrypoint") != ["/bin/sh", "-c"]:
        raise ComposeCandidateContractError(
            "Map application 300 service entrypoint is invalid"
        )
    command = service.get("command")
    if not isinstance(command, list) or len(command) != 1:
        raise ComposeCandidateContractError(
            "Map application 300 service command is invalid"
        )
    script = command[0]
    if not isinstance(script, str):
        raise ComposeCandidateContractError(
            "Map application 300 service command is invalid"
        )
    # 두 명령을 **이 순서로** 요구한다. 스키마를 올리기 전에 권한을 재조정하면
    # 아직 없는 relation에 GRANT를 내게 된다.
    expected_script_lines = (
        "set -eu",
        "/usr/local/bin/python -I -m alembic upgrade head",
        "/usr/local/bin/python -I -m kortravelmap.infra.runtime_privileges",
    )
    if tuple(line.strip() for line in script.strip().splitlines()) != expected_script_lines:
        raise ComposeCandidateContractError(
            "Map application 300 service command is invalid"
        )
    if service.get("restart") != "no" or service.get("profiles") != ["bootstrap"]:
        raise ComposeCandidateContractError(
            "Map application 300 service lifecycle is invalid"
        )
    if resolved and _IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        raise ComposeCandidateContractError(
            "Map application 300 resolved image identity is invalid"
        )


#: 계약 기계는 **값 동등**을 비교하므로 "키가 추가됐다"를 표현하지 못한다. 그래서
#: 서버 기본 인증을 통째로 갈아치우는 키는 이름 자체를 금지한다. 정본 compose는
#: 이 키를 어디에도 쓰지 않는다(2026-09-17 전수 확인) — 쓸 일이 생기면 계약표에
#: 명시적으로 넣고 이 집합에서 빼라. 값이 아니라 **존재**를 막는 것이 요점이다.
_FORBIDDEN_AUTH_OVERRIDE_ENV_NAMES = frozenset({"POSTGRES_HOST_AUTH_METHOD"})

#: 이 키는 **금지가 아니라 고정**이다. 정본 compose의 PostgreSQL 넷이 모두 쓰고,
#: 값이 `--auth-host=trust`면 fresh PGDATA에서 인증이 통째로 꺼진다.
_POSTGRES_INITDB_ARGS_ENV = "POSTGRES_INITDB_ARGS"


def _service_environment_items(service: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    """서비스의 `environment`를 **문법에 무관하게** (이름, 값) 목록으로 편다.

    Compose는 두 문법을 모두 받는다:

        environment: {NAME: value}
        environment: ["NAME=value", "NAME"]

    리스트 형태를 건너뛰면 전역 env 술어가 raw 층에서 **전역이 아니게 된다**
    (적대 리뷰 2026-09-18 F4: `environment: ["POSTGRES_HOST_AUTH_METHOD=trust"]`가
    raw를 통과했다). 오늘 최종적으로 막히는 이유는 `docker compose config`가
    리스트를 맵으로 정규화해 주기 때문뿐이라, resolved에만 의존하는 방어였다.

    값이 없는 항목(`"NAME"`, 즉 셸에서 물려받는 형태)은 값 `None`으로 낸다 —
    **이름의 존재**를 묻는 술어에는 그것으로 충분하고, 값을 묻는 술어는 `None`을
    "정본이 아님"으로 다루면 된다.
    """

    environment = service.get("environment")
    if isinstance(environment, Mapping):
        # 매핑형의 `None` 값도 리스트형 bare 이름과 **같은 것**이다 —
        # `docker compose config`가 `environment: [NAME]`을 `{"NAME": null}`로
        # 정규화한다(적대 리뷰 2026-09-18 C-F7 실측). 그래서 resolved 층에서 `None`은
        # 실제로 발생하고, 값을 묻는 술어는 그것을 "정본 아님"으로 봐야 한다.
        return [
            (str(name), None if value is None else str(value))
            for name, value in environment.items()
        ]
    if isinstance(environment, list):
        items: list[tuple[str, str | None]] = []
        for entry in environment:
            if not isinstance(entry, str):
                # **조용히 건너뛰지 않는다.** 리스트 안의 dict나 숫자는 이 함수가
                # 읽을 수 없는 형태이고, 그때 "env가 없다"로 보면 위의 전역 술어들이
                # 볼 재료를 잃는다 — 오늘 최종적으로 막히는 이유는 `docker compose
                # config`가 그 형태를 거부하기 때문뿐이라, 그것이 바로 F4가
                # "방어가 아니다"라고 판정한 의존이다(적대 리뷰 2026-09-18 D-F10).
                raise ComposeCandidateContractError(
                    "compose candidate declares an unreadable environment entry"
                )
            name, separator, value = entry.partition("=")
            items.append((name, value if separator else None))
        return items
    if environment is None:
        return []
    raise ComposeCandidateContractError(
        "compose candidate declares an unreadable environment shape"
    )


def _normalize_postgres_setting_name(name: str) -> str:
    """postgres가 GUC 이름을 읽는 방식과 같게 정규화한다.

    GUC 이름은 **대소문자를 구분하지 않고**, long option 형태(`--hba-file=`)에서는
    하이픈이 밑줄과 같다.
    """

    return name.strip().lower().replace("-", "_")


#: 정본 compose의 PostgreSQL 서버 넷이 쓰는 **최상위 키 전부**(실측 2026-09-18).
#: `shm_size`는 하나만 쓰지만 정당하므로 포함한다. `init`·`stop_grace_period`는
#: ADR-52(2026-09-28)로 공용 instance가 쓴다 — 둘 다 권한을 넓히지 않는다(init은
#: PID 1에 docker-init을 둘 뿐이고, grace는 종료 대기 시간이다).
#:
#: **금지 목록이 아니라 허용 목록이다.** 앞선 세 라운드가 같은 병으로 뚫렸다 —
#: `entrypoint`·`privileged`·`user`·`cap_add`·`pid`를 막았더니 `devices: /dev/sda`가
#: 통과했고(호스트 root), `docs/tasks.md`가 위험 키 열 개를 이미 열거해 뒀는데 그
#: 목록이 계속 뒤처졌다. 이 방향은 **다음 compose 스펙이 추가하는 키에 대해서도
#: fail-close**다. 새 키가 필요해지면 여기 한 줄을 명시적으로 더하게 하라.
#:
#: `entrypoint`가 없는 것이 의도다 — 그것으로 entrypoint 금지가 자동 성립하고
#: 기계가 하나로 줄어든다(entrypoint를 주면 아래 command 규칙이 무의미해진다).
_POSTGRES_ALLOWED_SERVICE_KEYS: Final = frozenset(
    {
        "command",
        "container_name",
        "environment",
        "healthcheck",
        "image",
        "init",
        "network_mode",
        "ports",
        "restart",
        "secrets",
        "shm_size",
        "stop_grace_period",
        "volumes",
    }
)

#: 정본 넷의 `command`가 `-c`로 설정하는 GUC 전부(실측). 이 밖의 설정은 거부한다 —
#: 그래서 `hba_file`·`ident_file`·`password_encryption`·`config_file`·`data_directory`·
#: `external_pid_file`을 **따로 열거하지 않아도** 전부 막힌다.
_POSTGRES_ALLOWED_COMMAND_SETTINGS: Final = frozenset(
    {
        "checkpoint_completion_target",
        "effective_cache_size",
        "listen_addresses",
        "maintenance_work_mem",
        "max_wal_size",
        "pg_prewarm.autoprewarm",
        "pg_stat_statements.max",
        "pg_stat_statements.track",
        "random_page_cost",
        "shared_buffers",
        "shared_preload_libraries",
        "work_mem",
    }
)

_POSTGRES_SERVER_COMMAND = "postgres"
_POSTGRES_LISTEN_SETTING: Final = "listen_addresses"
_POSTGRES_CANONICAL_LISTEN_VALUE: Final = "127.0.0.1"
#: 초기화 위치를 바꾸는 env. 명령행 축은 위 허용 목록이 덮는다(`-D`는 아래 파서가
#: `data_directory`로 매핑하고, 그 이름이 허용 목록에 없다).
_POSTGRES_FORBIDDEN_LAYOUT_ENV_NAMES: Final = frozenset({"PGDATA"})
#: 보조 식별 축. official entrypoint는 이 둘 중 하나가 없으면 빈 PGDATA에서 initdb를
#: 돌리지 않는다.
_POSTGRES_CLUSTER_INIT_ENV_NAMES: Final = frozenset(
    {"POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE"}
)
#: `role`이 이것으로 끝나는 Manager 컨테이너의 `compose_service`가 declared 집합이다.
_POSTGRES_DECLARED_ROLE_SUFFIX: Final = "postgresql"


def _postgres_command_tokens(command: object) -> list[str] | None:
    """`command`를 토큰 목록으로. 문자열도 받고, 알 수 없는 모양이면 `None`."""

    if isinstance(command, list):
        if any(not isinstance(item, str) for item in command):
            return None
        return list(command)
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except ValueError:
            return None
    return None


def _postgres_command_settings(command: object) -> list[tuple[str, str]] | None:
    """`command`가 설정하는 (정규화된 GUC 이름, 값) 전부. **모르는 토큰이 있으면 `None`.**

    postgres의 실제 인자 처리를 따른다. 앞선 판은 `-c name=value`와 `--name=value`
    둘만 읽었고, 그래서 실제 서버가 honor하는 다음 형태가 전부 통과했다(적대 리뷰
    2026-09-18 F2 실측):

        -i                     한 토큰. `listen_addresses = '*'`
        -h 0.0.0.0 / -h0.0.0.0 붙여쓴 short option
        -clisten_addresses=…   붙여쓴 getopt 형
        -chba_file=…           금지 설정을 붙여쓰기 한 번으로 우회

    `None`을 돌려주는 것이 요점이다 — 호출부가 그것을 **거부**로 다룬다. 모르는
    토큰을 조용히 건너뛰면 그 토큰이 곧 우회로가 된다.
    """

    tokens = _postgres_command_tokens(command)
    if tokens is None or not tokens:
        return None
    head, *rest = tokens
    if PurePosixPath(head).name != _POSTGRES_SERVER_COMMAND:
        return None

    settings: list[tuple[str, str]] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        index += 1
        if token == "-i":
            # `-i`는 값이 없다. postgres는 이것을 `listen_addresses = '*'`로 읽는다.
            settings.append((_POSTGRES_LISTEN_SETTING, "*"))
            continue
        if token.startswith("--") and "=" in token:
            name, _separator, value = token[2:].partition("=")
            settings.append((_normalize_postgres_setting_name(name), value.strip()))
            continue
        # short option: 값이 붙어 있거나 다음 토큰이다.
        if len(token) >= 2 and token[0] == "-" and token[1] != "-":
            flag = token[1]
            payload = token[2:]
            if not payload:
                if index >= len(rest):
                    return None
                payload = rest[index]
                index += 1
            if flag == "c":
                if "=" not in payload:
                    return None
                name, _separator, value = payload.partition("=")
                settings.append(
                    (_normalize_postgres_setting_name(name), value.strip())
                )
                continue
            mapped = _POSTGRES_SHORT_OPTION_SETTINGS.get(flag)
            if mapped is None:
                return None
            settings.append((mapped, payload.strip()))
            continue
        return None
    return settings


#: postgres의 short option → 그것이 설정하는 GUC. `-p`만 허용 목록 안이고 나머지는
#: 이름이 허용 목록에 없어서 자동으로 거부된다 — 매핑을 두는 이유는 **무엇을 설정하는
#: 지 말하게** 해서 `-h`/`-D`가 조용히 통과하지 않게 하는 것이다.
_POSTGRES_SHORT_OPTION_SETTINGS: Final = MappingProxyType(
    {
        "p": "port",
        "h": _POSTGRES_LISTEN_SETTING,
        "D": "data_directory",
        "k": "unix_socket_directories",
    }
)
#: `-p`는 정본 넷이 전부 쓰므로 GUC 허용 목록과 별도로 허용한다(값은 포트 핀이
#: 소유한다 — PinVi는 exact-match, 나머지는 대역 문서가 본다).
_POSTGRES_ALLOWED_COMMAND_EXTRA_SETTINGS: Final = frozenset({"port"})


def _declared_postgres_compose_services() -> frozenset[str]:
    """`config/docker-targets.yml`이 PostgreSQL이라고 **선언한** Manager compose 서비스.

    GM-17 A가 이 문서를 자리 고정·무결성·값 정책으로 신뢰시켰다(trusted 설치본에서
    env redirect 거부 + root 소유·비쓰기 강제). 그래서 후보 문서의 철자에 의존하지
    않는 **declared 축**이 여기서 나온다.

    외부 프로젝트 컨테이너는 제외한다 — 그쪽 compose는 이 저장소의 후보가 아니다.
    설정을 읽을 수 없으면 빈 집합을 돌려준다(witnessed 축이 남는다).
    """

    try:
        config = load_targets_config()
    except Exception:  # noqa: BLE001 - 설정을 못 읽으면 witnessed 축만 쓴다
        return frozenset()
    declared: set[str] = set()
    for spec in (config.get("containers") or {}).values():
        if not isinstance(spec, Mapping) or spec.get("external_project"):
            continue
        role = spec.get("role")
        if isinstance(role, str) and role.endswith(_POSTGRES_DECLARED_ROLE_SUFFIX):
            service = spec.get("compose_service")
            if isinstance(service, str) and service:
                declared.add(service)
    return frozenset(declared)


#: 셸로 인식하는 프로그램 이름. 이 뒤의 `-c` 페이로드는 그 자체가 명령줄이다.
_COMMAND_SHELL_NAMES: Final = frozenset({"sh", "bash", "dash", "ash", "busybox"})


def _command_program_names(command: object) -> list[str]:
    """이 `command`가 **실행하는 프로그램**의 이름들(basename).

    물어야 할 것은 "문서에 그 낱말이 있는가"가 아니라 "무엇이 실행되는가"다. 첫 판은
    토큰을 전부 쪼개 봤는데 `psql -d postgres -tAc …`의 **데이터베이스 이름**이 흔적으로
    잡혀 정당한 one-shot(`pinvi-db-init`)이 죽었다.

    그래서 프로그램 자리만 본다 — `argv[0]`, 셸이면 그 `-c` 페이로드의 첫 낱말, 그리고
    `exec` 바로 뒤. `sh -c 'exec postgres -i'`가 그 셋째 경우다.
    """

    tokens = _postgres_command_tokens(command)
    if not tokens:
        return []
    names = [PurePosixPath(tokens[0]).name]
    if names[0] not in _COMMAND_SHELL_NAMES:
        return names
    payload: str | None = None
    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith("-") and "c" in token.lstrip("-"):
            if index + 1 < len(tokens):
                payload = tokens[index + 1]
            break
    if payload is None:
        return names
    try:
        inner = shlex.split(payload)
    except ValueError:
        inner = payload.split()
    for index, word in enumerate(inner):
        if index == 0 or (index > 0 and inner[index - 1] == "exec"):
            names.append(PurePosixPath(word).name)
    return names


def _service_witnesses_a_postgres_server(
    service: Mapping[str, Any], declared_env: Mapping[str, Any]
) -> bool:
    """후보 문서 자체가 "여기 postgres 서버가 있다"고 말하는가.

    **basename으로 본다.** 앞선 판은 `command[0] == "postgres"` 리터럴 비교여서
    `/usr/local/bin/postgres`·문자열 command·`sh -c 'exec postgres …'`가 판정을
    피했다(적대 리뷰 2026-09-18 F1 실측).

    이미지 문자열은 **판정 재료로 쓰지 않는다** — digest 핀·리네임에서 깨지고,
    리뷰어가 map-postgres의 resolved 층에서 마커가 아예 꺼지는 것까지 실측했다
    (raw에서 켜진 이유는 플레이스홀더 문자열이 우연히 "POSTGRES"를 담고 있었기
    때문이다).
    """

    if any(
        name == _POSTGRES_SERVER_COMMAND
        for name in _command_program_names(service.get("command"))
    ):
        return True
    return any(name in declared_env for name in _POSTGRES_CLUSTER_INIT_ENV_NAMES)


def postgres_server_services(resolved: Mapping[str, Any]) -> frozenset[str]:
    """이 resolved 문서에서 PostgreSQL **서버**인 compose 서비스 = declared ∪ witnessed.

    이름을 들지 않는다. declared는 신뢰된 `config/docker-targets.yml`의 role에서, witnessed는
    문서가 스스로 드러내는 서버 실행 형태에서 온다(`_service_witnesses_a_postgres_server`).
    문서에 없는 declared 이름은 뺀다 — compose가 그 서비스를 다룰 수 없다.

    서비스 목록을 읽을 수 없으면 빈 집합이 아니라 거부다 — 빈 집합은 "PostgreSQL 없음"으로
    읽혀 이것에 기대는 울타리(R3)를 조용히 끈다.
    """

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("compose services mapping is unreadable")
    declared = _declared_postgres_compose_services()
    found: set[str] = set()
    for service_name, service in services.items():
        if not isinstance(service_name, str) or not isinstance(service, Mapping):
            continue
        if service_name in declared or _service_witnesses_a_postgres_server(
            service, dict(_service_environment_items(service))
        ):
            found.add(service_name)
    return frozenset(found)


def postgres_server_port(service: Mapping[str, Any]) -> int | None:
    """PostgreSQL 서버 서비스가 `command`로 듣는 포트. 읽을 수 없으면 ``None``.

    서버 명령을 읽는 파서는 C6c의 것 하나다(`_postgres_command_settings`) — `-p N`·`-pN`·
    `--port=N`이 모두 `port`가 되고, postgres처럼 **마지막 값**이 이긴다. 모르는 토큰이 있거나
    보간되지 않은 값(raw의 `${…}`)이면 포트를 모른다.
    """

    settings = _postgres_command_settings(service.get("command"))
    if settings is None:
        return None
    ports = [value for name, value in settings if name == "port"]
    if not ports or not ports[-1].isdigit():
        return None
    port = int(ports[-1])
    return port if 1 <= port <= 65535 else None


def postgres_server_services_on_port(
    resolved: Mapping[str, Any],
    port: int,
) -> tuple[str, ...]:
    """이 resolved 문서에서 ``port``를 듣는 PostgreSQL 서버 서비스들(이름순).

    Map·PinVi DB가 어느 instance에 사는지는 이것으로 **DSN 포트에서** 유도한다(ADR-53). 호출자는
    정확히 하나를 요구한다 — 없거나 둘이면 DSN이 가리키는 instance를 말할 수 없다.
    """

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("compose services mapping is unreadable")
    return tuple(
        sorted(
            name
            for name in postgres_server_services(resolved)
            if postgres_server_port(services[name]) == port
        )
    )


#: compose가 secret을 붙이는 기본 디렉터리. 상대 `target`은 이 아래다.
_COMPOSE_SECRETS_DIRECTORY: Final = "/run/secrets"


def _secret_reference_mount(reference: object) -> tuple[str, str] | None:
    """서비스 `secrets[]` 한 항목의 (source, 컨테이너 안 절대 경로). 읽을 수 없으면 ``None``.

    짧은 문법(`- name`)은 `/run/secrets/name`에, 상대 `target`은 `/run/secrets/<target>`에
    붙는다 — raw와 resolved가 같은 답을 내게 compose의 규칙을 그대로 따른다.
    """

    if isinstance(reference, str):
        source: object = reference
        target: object = reference
    elif isinstance(reference, Mapping):
        source = reference.get("source")
        target = reference.get("target") or source
    else:
        return None
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        return None
    if not target.startswith("/"):
        target = f"{_COMPOSE_SECRETS_DIRECTORY}/{target}"
    return source, target


def _service_secret_sources(service: Mapping[str, Any]) -> frozenset[str]:
    """서비스가 마운트하는 secret의 source 이름들. 읽을 수 없는 항목은 거부한다."""

    references = service.get("secrets")
    if references is None:
        return frozenset()
    if not isinstance(references, list):
        raise ComposeCandidateContractError("compose service secrets are unreadable")
    sources: set[str] = set()
    for reference in references:
        mount = _secret_reference_mount(reference)
        if mount is None:
            raise ComposeCandidateContractError("compose service secrets are unreadable")
        sources.add(mount[0])
    return frozenset(sources)


@dataclass(frozen=True)
class PostgresAdminSecret:
    """PostgreSQL 서버 서비스의 admin password secret — 이름 목록 없이 문서에서 유도한다.

    `source`는 최상위 `secrets`의 키이고, `environment`는 그 secret이 값을 읽는 `.env` 변수다.
    """

    source: str
    environment: str


def postgres_admin_secret(
    document: Mapping[str, Any],
    service_name: str,
) -> PostgresAdminSecret:
    """``POSTGRES_PASSWORD_FILE`` → 그 경로에 붙는 `secrets[]` 항목의 source → 최상위
    ``secrets.<source>.environment``를 따라 instance admin secret을 낸다.

    raw와 resolved 모두에서 같은 답이다(`POSTGRES_PASSWORD_FILE`은 보간하지 않는 리터럴이고,
    secret mount 규칙은 `_secret_reference_mount`가 compose와 같게 읽는다). 어느 고리든 끊기면
    거부한다 — admin password를 파일이 아닌 env로 받는 서버는 이 계약 밖이다.
    """

    services = document.get("services")
    service = services.get(service_name) if isinstance(services, Mapping) else None
    if not isinstance(service, Mapping):
        raise ComposeCandidateContractError(
            "PostgreSQL instance admin secret is not derivable: "
            + _describe_candidate_service_key(service_name)
        )
    password_file = dict(_service_environment_items(service)).get("POSTGRES_PASSWORD_FILE")
    references = service.get("secrets")
    mounts = [
        mount
        for reference in (references if isinstance(references, list) else [])
        if (mount := _secret_reference_mount(reference)) is not None
        and mount[1] == password_file
    ]
    top_level = document.get("secrets")
    declared = (
        top_level.get(mounts[0][0])
        if len(mounts) == 1 and isinstance(top_level, Mapping)
        else None
    )
    environment_name = declared.get("environment") if isinstance(declared, Mapping) else None
    if (
        not password_file
        or len(mounts) != 1
        or not isinstance(environment_name, str)
        or not environment_name
    ):
        raise ComposeCandidateContractError(
            "PostgreSQL instance admin secret is not derivable: "
            + _describe_candidate_service_key(service_name)
        )
    return PostgresAdminSecret(source=mounts[0][0], environment=environment_name)


#: 클러스터 서비스의 `healthcheck` payload에 나타나는 **프로그램 자리**. 정본 넷을
#: 실측해 얻었다 — `pg_isready`, 그리고 map이 쓰는 `test "$(cat /proc/1/comm)" = postgres`.
#:
#: 허용 목록이 **키 이름만** 묶었던 것이 적대 리뷰 2026-09-18 라운드4 F5다.
#: `["CMD-SHELL", "psql -U postgres -c \"ALTER ROLE postgres PASSWORD ...\""]`가 통과했고,
#: 컨테이너 안 소켓은 `local all all trust`라 그것이 superuser 실행이다.
_POSTGRES_HEALTHCHECK_PROGRAMS: Final = frozenset({"pg_isready", "test", "cat"})
#: 셸 payload를 조각으로 자르는 구분자. 각 조각의 첫 낱말이 프로그램 자리다.
_SHELL_FRAGMENT_SEPARATORS: Final = ("&&", "||", ";", "|", "\n", "$(", "`")


def _shell_program_positions(payload: str) -> list[str]:
    """셸 문장에서 **프로그램 자리**의 basename들.

    연산자와 명령 치환으로 조각을 내고 각 조각의 첫 낱말을 본다. 이름을 금지하는
    방향(`psql`을 막는다)이 아니라 **허용하는** 방향이어야 한다 — 금지 목록은 이
    파일에서 세 라운드 연속으로 뒤처졌다.
    """

    fragments = [payload]
    for separator in _SHELL_FRAGMENT_SEPARATORS:
        nested: list[str] = []
        for fragment in fragments:
            nested.extend(fragment.split(separator))
        fragments = nested
    names: list[str] = []
    for fragment in fragments:
        words = fragment.replace(")", " ").split()
        if words:
            names.append(PurePosixPath(words[0].strip("\"'")).name)
    return names


def _assert_postgres_healthcheck_is_canonical(
    service_name: str, service: Mapping[str, Any]
) -> None:
    """클러스터 서비스의 healthcheck가 **정본 프로그램만** 부르는가."""

    healthcheck = service.get("healthcheck")
    if not isinstance(healthcheck, Mapping):
        return
    test = healthcheck.get("test")
    if test is None:
        return
    tokens = test if isinstance(test, list) else [test]
    for token in tokens:
        if not isinstance(token, str):
            raise ComposeCandidateContractError(
                "compose candidate PostgreSQL healthcheck is not a string command: "
                + _describe_candidate_service_key(service_name)
            )
    payload_words: list[str] = []
    if tokens and tokens[0] == "CMD":
        # exec 형식: 프로그램 자리는 argv[1] 하나뿐이다. 나머지는 그 프로그램의 인자이고
        # 어떤 셸도 그것을 해석하지 않으므로 다른 프로그램을 띄울 수 없다 — 인자를
        # 셸 payload로 읽으면 `-h`·`127.0.0.1`이 "프로그램"으로 오판돼 정본 exec
        # probe(ADR-52)가 거부된다.
        payload_words = [PurePosixPath(tokens[1]).name] if len(tokens) > 1 else [""]
    else:
        for index, token in enumerate(tokens):
            if index == 0 and token in {"CMD-SHELL", "NONE"}:
                continue
            payload_words.extend(_shell_program_positions(token))
    for name in payload_words:
        if name not in _POSTGRES_HEALTHCHECK_PROGRAMS:
            raise ComposeCandidateContractError(
                f"compose candidate PostgreSQL healthcheck runs a non-canonical "
                f"program {name}: " + _describe_candidate_service_key(service_name)
            )


def _assert_one_postgres_cluster_runtime(
    service_name: str, service: Mapping[str, Any], *, declared: bool
) -> None:
    """PostgreSQL 서버의 **형태 전체**를 허용 목록으로 묶는다.

    금지 목록은 세 라운드 연속으로 뒤처졌다 — 매번 내가 놓친 철자·키가 우회로였다.
    정본 넷의 형태는 좁고 고정적이므로(최상위 키 13개, GUC 12개 + `-p`) 방향을
    뒤집으면 **모르는 것이 하나라도 있으면 거부**가 되고, 다음 compose 스펙이나
    postgres 버전이 무엇을 추가해도 fail-close다.
    """

    if not declared:
        # 후보가 서버를 세우는데 신뢰된 문서가 그것을 PostgreSQL로 선언하지 않았다.
        # `role`은 UI 문자열이라 아무것도 강제하지 않으므로, 이 불일치 자체를
        # 거부하는 것이 declared 축을 항진명제에서 빼낸다.
        raise ComposeCandidateContractError(
            "compose candidate runs an undeclared PostgreSQL server: "
            + _describe_candidate_service_key(service_name)
        )

    # **키 존재가 아니라 값이 있음으로 본다.** `docker compose config`가 resolved
    # 문서의 모든 서비스에 `entrypoint: null`·`command: null` 같은 빈 키를 붙이기
    # 때문이다 — 키로 판정하면 정본 넷이 resolved 층에서 전부 거부된다(실측).
    # concierge 신호 집합에서 이미 겪은 함정이고, 같은 처방이다.
    unknown_keys = sorted(
        name
        for name, value in service.items()
        if name not in _POSTGRES_ALLOWED_SERVICE_KEYS and value is not None
    )
    if unknown_keys:
        raise ComposeCandidateContractError(
            f"compose candidate gives a PostgreSQL service non-canonical keys "
            f"{unknown_keys}: " + _describe_candidate_service_key(service_name)
        )

    _assert_postgres_healthcheck_is_canonical(service_name, service)
    settings = _postgres_command_settings(service.get("command"))
    if settings is None:
        raise ComposeCandidateContractError(
            "compose candidate PostgreSQL service must run the canonical postgres "
            "command: " + _describe_candidate_service_key(service_name)
        )
    allowed = (
        _POSTGRES_ALLOWED_COMMAND_SETTINGS | _POSTGRES_ALLOWED_COMMAND_EXTRA_SETTINGS
    )
    for name, _value in settings:
        if name not in allowed:
            raise ComposeCandidateContractError(
                f"compose candidate PostgreSQL command sets a non-canonical "
                f"{name}: " + _describe_candidate_service_key(service_name)
            )
    bindings = [value for name, value in settings if name == _POSTGRES_LISTEN_SETTING]
    # **모든** `listen_addresses`가 loopback이어야 한다 — postgres는 같은 설정이
    # 여러 번 오면 마지막을 쓴다.
    if not bindings or any(
        value != _POSTGRES_CANONICAL_LISTEN_VALUE for value in bindings
    ):
        raise ComposeCandidateContractError(
            "compose candidate PostgreSQL service must keep the loopback binding: "
            + _describe_candidate_service_key(service_name)
        )
    # `PGDATA`와 `env_file`은 여기서 보지 않는다. 앞의 허용 목록이 `env_file`을 막고,
    # `PGDATA`는 `_assert_canonical_postgres_initdb_args`가 소유한다 — 두 분기 모두
    # **도달하지 않는 코드**였다(적대 리뷰 2026-09-18 라운드4 F13 실측). 닿지 않는
    # 분기는 안전이 아니라, 그 규칙의 자리가 어디인지에 대한 거짓 신호다.


def _assert_postgres_cluster_runtime_is_canonical(document: Mapping[str, Any]) -> None:
    """문서 전역에서 PostgreSQL 서버의 실행 형태를 묶는다.

        in_scope = declared OR witnessed

    `declared`는 신뢰된 문서(`config/docker-targets.yml`)의 `role`에서, `witnessed`는
    후보가 스스로 드러내는 것에서 온다. 논리합이라 어느 한쪽을 피해도 다른 쪽이 남고,
    **witnessed인데 declared가 아니면** 그 불일치 자체를 거부한다.
    """

    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    declared_services = _declared_postgres_compose_services()
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            continue
        declared_env = dict(_service_environment_items(service))
        is_declared = service_name in declared_services
        if not (
            is_declared or _service_witnesses_a_postgres_server(service, declared_env)
        ):
            continue
        _assert_one_postgres_cluster_runtime(service_name, service, declared=is_declared)


#: 컨테이너에 **호스트 권한을 주는** compose 키. 정본 34 서비스 실측(2026-09-18)에서
#: 이 중 열한 개는 사용 0건이고, 세 개(`privileged`·`devices`·`user`)만 아래 예외에
#: 적힌 서비스가 쓴다.
#:
#: **방향이 계약표와 반대다.** 계약표는 "이 서비스의 이 값은 이래야 한다"를 열거해서,
#: 열거되지 않은 서비스가 무방비였다(적대 리뷰 2026-09-18 F1이 그것으로 뚫었다).
#: 이쪽은 "이 키는 어디서도 금지, 단 열거된 자리만 예외"다 — 새 서비스가 이 키를
#: 들면 **기본이 거부**이므로 열거가 늘어나도 fail-close가 유지된다.
_FORBIDDEN_PRIVILEGE_KEYS: Final = (
    "cap_add",
    "cgroup",
    "cgroup_parent",
    "device_cgroup_rules",
    "devices",
    "group_add",
    "ipc",
    "links",
    "pid",
    "privileged",
    "runtime",
    "security_opt",
    "sysctls",
    "user",
    "userns_mode",
    "uts",
    "volumes_from",
)

#: `deploy.resources.reservations.devices`는 중첩이라 최상위 키 스캔에 걸리지 않는다.
#: `docker compose config`가 그 값을 그대로 낸다(실측).
_FORBIDDEN_NESTED_PRIVILEGE_PATH: Final = ("deploy", "resources", "reservations", "devices")

#: **값을 봐야 하는 키.** 이 둘은 하드닝에도 쓰인다 — 능력을 버리거나 권한 상승을
#: 막는 것은 특권 **부여**가 아니다. 첫 판은 키 존재만 봐서 `cap_drop: [ALL]`·
#: `security_opt: [no-new-privileges:true]`·`user: 65534`를 전부 거부했다(적대 리뷰
#: 2026-09-18 라운드4 F12 — 하드닝을 금지하는 규칙이었다).
_ROOT_USER_VALUES: Final = frozenset({"0", "root", "0:0", "root:root"})
_UNCONFINED_SECURITY_OPT: Final = "unconfined"


def _privilege_key_grants_host_access(key: str, value: object) -> bool:
    """이 (키, 값)이 실제로 **호스트 권한을 주는가.**

    `user`는 root일 때만, `security_opt`는 `*:unconfined`/`systempaths=unconfined`일
    때만 위험하다. 나머지 키는 값이 truthy이면 위험하다.
    """

    if key == "user":
        return str(value).strip() in _ROOT_USER_VALUES
    if key == "security_opt":
        entries = value if isinstance(value, list) else [value]
        return any(
            _UNCONFINED_SECURITY_OPT in str(entry).lower() for entry in entries
        )
    return bool(value)

#: 정본이 실제로 쓰는 (서비스, 키) 쌍. 실측으로 얻었고, 늘리려면 여기 한 줄을
#: 명시적으로 더해야 한다 — 그 마찰이 이 규칙의 값어치다.
#: `test_the_privilege_exception_set_is_pinned`가 이 집합을 리터럴로 못박는다.
_ALLOWED_PRIVILEGE_KEY_PAIRS: Final = frozenset(
    {
        ("cadvisor", "privileged"),
        ("cadvisor", "devices"),
        ("prometheus", "user"),
        ("grafana", "user"),
        # weather의 자체 Prometheus(ADR-47)도 host-mode 포트 바인딩·데이터 디렉터리
        # 소유권 때문에 정본 prometheus와 같은 이유로 root user가 필요하다 — 이름이
        # 다를 뿐 `prom/prometheus` 신원은 같다(아래 접두 표).
        ("kor-travel-weather-prometheus", "user"),
    }
)

#: 예외는 **이름만으로 성립하지 않는다.** 정본 cadvisor의 mount와 `privileged: true`를
#: 그대로 두고 `image`만 바꾸면 한 줄로 호스트 root였다(적대 리뷰 2026-09-18 라운드4
#: F3 실측). 그래서 그 서비스의 **신원**까지 본다.
#:
#: 발행자/이름 접두로 묶고 버전은 묶지 않는다 — 버전 bump는 정당한 변경이고, 막으려는
#: 것은 **임의 이미지**다. 정본은 `${CADVISOR_IMAGE:-gcr.io/cadvisor/cadvisor:v0.52.1}`
#: 처럼 placeholder 안에 기본값을 담으므로 raw·resolved 양쪽에서 이 접두가 보인다.
_PRIVILEGE_EXCEPTION_IMAGE_PREFIXES: Final = MappingProxyType(
    {
        "cadvisor": "gcr.io/cadvisor/cadvisor:",
        "prometheus": "prom/prometheus:",
        "grafana": "grafana/grafana:",
        "kor-travel-weather-prometheus": "prom/prometheus:",
    }
)


def _privilege_exception_applies(
    service_name: str, key: str, service: Mapping[str, Any]
) -> bool:
    """이 서비스가 그 특권 키를 쓸 **자격이 있는가.**

    이름이 목록에 있는 것만으로는 부족하다 — 그 이름으로 임의 이미지를 세우면 예외가
    공격자에게 상속된다.
    """

    if (service_name, key) not in _ALLOWED_PRIVILEGE_KEY_PAIRS:
        return False
    expected_prefix = _PRIVILEGE_EXCEPTION_IMAGE_PREFIXES.get(service_name)
    if expected_prefix is None:
        return False
    image = service.get("image")
    return isinstance(image, str) and expected_prefix in image


def _assert_no_host_privilege_escalation(document: Mapping[str, Any]) -> None:
    """어떤 서비스도 호스트 권한을 가져갈 수 없다 — 열거된 예외만 빼고.

    리뷰어 둘이 각각 실측했다: `concierge-api`에 `privileged: true` + `pid: host`를
    준 후보가 계약을 전무로 통과하고 `ktdctl deploy conc`가 그것을 띄운다 = 호스트
    root. concierge 게이트의 "구성됨" 신호 집합에 그 키들이 없기 때문이고, 신호
    집합을 넓히는 것으로는 이 축이 닫히지 않는다(배포는 target의 서비스 목록으로
    도는데 `image`만 있는 서비스도 실제로 뜬다).

    **falsy는 부재로 본다.** `docker compose config`가 resolved 문서에 `privileged:
    false`·`user: ""`·`cap_add: []`를 붙이므로 키 존재로 판정하면 정본이 거부된다 —
    같은 함정을 concierge 신호와 PostgreSQL 허용 목록에서 두 번 겪었다.

    **전역 불변식이다. 어떤 서비스의 존재에도 게이팅하지 마라.**
    """

    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            continue
        nested = service
        for segment in _FORBIDDEN_NESTED_PRIVILEGE_PATH:
            nested = nested.get(segment) if isinstance(nested, Mapping) else None
            if nested is None:
                break
        if nested:
            raise ComposeCandidateContractError(
                "compose candidate grants host privilege with "
                f"{'.'.join(_FORBIDDEN_NESTED_PRIVILEGE_PATH)}: "
                + _describe_candidate_service_key(service_name)
            )
        for key in _FORBIDDEN_PRIVILEGE_KEYS:
            if not _privilege_key_grants_host_access(key, service.get(key)):
                continue
            if _privilege_exception_applies(service_name, key, service):
                continue
            raise ComposeCandidateContractError(
                f"compose candidate grants host privilege with {key}: "
                + _describe_candidate_service_key(service_name)
            )


def _assert_no_postgres_auth_override(document: Mapping[str, Any]) -> None:
    """서버 기본 인증을 갈아치우는 env 키를 **어느 서비스에서도** 금지한다.

    `POSTGRES_HOST_AUTH_METHOD=trust`는 `pg_hba.conf`의 host 행을 통째로 대체해
    비밀번호 없이 접속을 허용한다. 2026-09-17 감사 실측: 이 키는 Map·PinVi **양쪽**
    에서 raw·resolved·UI 저장 경로를 전부 통과했다 — 계약표가 값-동등 비교라
    **키 추가를 볼 수 없기** 때문이다.

    **전역 불변식이다.** 어떤 서비스의 존재에도 게이팅하지 마라 — 어느 서비스가
    그 키를 들고 있든 결과는 같다. (이 파일의 S2·S3가 같은 교훈으로 정리됐다.)
    """

    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            continue
        for name, _value in _service_environment_items(service):
            if name in _FORBIDDEN_AUTH_OVERRIDE_ENV_NAMES:
                raise ComposeCandidateContractError(
                    "compose candidate overrides PostgreSQL host authentication: "
                    + _describe_candidate_service_key(service_name)
                )


def _assert_canonical_postgres_initdb_args(document: Mapping[str, Any]) -> None:
    """`POSTGRES_INITDB_ARGS`를 선언한 **어떤 서비스든** 값이 정본이어야 한다.

    위 금지와 **대칭**이다. 그쪽은 키의 존재를 막고, 이쪽은 허용된 키의 값을 묶는다.
    둘 다 서비스 이름에 결박하지 않는다는 것이 요점이다.

    계약표(`_MAP_DATABASE_CANONICAL_ENV_VALUES`)가 이 일을 못 하는 이유는 그것이
    **서비스를 열거**하기 때문이다. 2026-09-17에 Map을 거기 넣어 막았는데, 정본
    compose에는 PostgreSQL이 **넷**이고 `kor-travel-geo-postgres`·
    `kor-travel-concierge-postgres`는 계약표에도 validator에도 없었다 — 적대 리뷰
    2026-09-18이 세 진입점(raw·resolved·UI 저장) 전부에서 `--auth-host=trust`가
    통과하는 것을 실측했다. fresh PGDATA에서 initdb가 `trust` 행을 pg_hba **첫
    행**으로 쓰므로 12500/12600에 비밀번호 없는 superuser가 생긴다. 그 값은 official
    entrypoint의 `eval`에 그대로 들어가므로 셸 주입 벡터이기도 하다.

    Map의 계약표 항목은 **그대로 둔다** — 그쪽은 UI 잠금
    (`_CONTRACT_LOCKED_ENV_NAMES_BY_SERVICE`)을 파생시키는 다른 일을 한다.

    **전역 불변식이다. 어떤 서비스의 존재에도 게이팅하지 마라.**
    """

    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            continue
        declared = dict(_service_environment_items(service))
        if (
            service_name in _declared_postgres_compose_services()
            or _service_witnesses_a_postgres_server(service, declared)
        ):
            # **부재는 `trust`와 같다.** initdb를 `--auth-host` 없이 부르면 기본이
            # `trust`이고, 그러면 pg_hba **첫 행**이 `host all all 127.0.0.1/32 trust`가
            # 된다 — first-match-wins라 뒤에 붙는 scram 행은 무의미하다. 실제
            # 컨테이너로 실측하면 "키 없음"과 "키=trust"의 pg_hba가 한 글자도 다르지
            # 않다(적대 리뷰 2026-09-18 C-F1). 그래서 값만 묻는 술어는 **더 짧은
            # payload**에 그대로 뚫린다.
            # official entrypoint는 `file_env 'POSTGRES_INITDB_ARGS'`를 부르므로
            # **`POSTGRES_INITDB_ARGS_FILE`도 같은 값을 준다**(적대 리뷰 2026-09-18
            # D-F7: 실제 이미지의 entrypoint 251행에서 확인했고, 그 형태로 `trust`
            # pg_hba가 만들어지는 것까지 실측했다). 이름으로 막는 술어는 이름의
            # **변형**까지 봐야 한다. `POSTGRES_HOST_AUTH_METHOD`는 `file_env`를
            # 거치지 않으므로(252행) 그쪽 변형은 대상이 아니다.
            if f"{_POSTGRES_INITDB_ARGS_ENV}_FILE" in declared:
                raise ComposeCandidateContractError(
                    f"compose candidate sources {_POSTGRES_INITDB_ARGS_ENV} from a "
                    "file the contract cannot read: "
                    + _describe_candidate_service_key(service_name)
                )
            if _POSTGRES_INITDB_ARGS_ENV not in declared:
                raise ComposeCandidateContractError(
                    f"compose candidate omits {_POSTGRES_INITDB_ARGS_ENV} on a "
                    "PostgreSQL service (absence selects trust authentication): "
                    + _describe_candidate_service_key(service_name)
                )
            for forbidden in sorted(_POSTGRES_FORBIDDEN_LAYOUT_ENV_NAMES):
                if forbidden in declared:
                    # 초기화 위치를 바꾸면 "fresh PGDATA에서만"이라는 전제를 공격자가
                    # 스스로 만들 수 있다.
                    raise ComposeCandidateContractError(
                        f"compose candidate relocates PostgreSQL data with "
                        f"{forbidden}: " + _describe_candidate_service_key(service_name)
                    )
            if _env_file_entries(service.get("env_file")):
                # `env_file`은 이 문서를 읽어서는 알 수 없는 값을 주입한다. 그러면
                # 위 두 검사가 볼 재료 자체가 사라진다 — 종전에는 이 금지가 **열거된
                # 서비스에만** 걸려서 geo/concierge가 통째로 빠져나갔다.
                raise ComposeCandidateContractError(
                    "compose candidate forbids env_file on a PostgreSQL service: "
                    + _describe_candidate_service_key(service_name)
                )
        for name, value in declared.items():
            if name != _POSTGRES_INITDB_ARGS_ENV:
                continue
            if value != _POSTGRES_CANONICAL_INITDB_ARGS:
                # 값을 문구에 넣지 않는다 — 거부 사유는 "정본이 아님"이고, 거부된
                # 값 자체는 운영자가 자기 후보에서 읽으면 된다.
                raise ComposeCandidateContractError(
                    "compose candidate declares non-canonical "
                    f"{_POSTGRES_INITDB_ARGS_ENV}: "
                    + _describe_candidate_service_key(service_name)
                )


def _service_holds_admin_secret(
    service: Mapping[str, Any],
    secret: PostgresAdminSecret,
    *,
    password: str,
) -> bool:
    """이 서비스가 instance admin secret을 드는가 — mount, 그 변수의 env 참조, 또는 그 값.

    raw 문서는 변수 **참조**(`${VAR}`, 값 없는 `VAR` key)로, resolved 문서는 보간된 **값**으로
    나타난다. 두 모양을 다 본다 — 한쪽만 보면 다른 쪽 경로가 뚫린다.
    """

    if secret.source in _service_secret_sources(service):
        return True
    for name, value in _service_environment_items(service):
        if value is None:
            if name == secret.environment:
                return True
            continue
        if secret.environment in variable_names(value):
            return True
        if password and password in value:
            return True
    return False


def _assert_instance_admin_secret_holders(
    document: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
    resolved: bool,
) -> None:
    """모든 PostgreSQL 서버의 admin secret은 그 instance와 one-shot만 든다(ADR-53).

    이름 목록이 없다. 서버는 declared ∪ witnessed(`postgres_server_services`)이고, admin secret은
    각 서버의 `POSTGRES_PASSWORD_FILE`에서 유도한다(`postgres_admin_secret`). 그 secret을
    마운트하거나 그 변수(resolved에서는 그 값)를 env에 드는 서비스는

    - 그 instance 자신이거나,
    - `restart: "no"`인 one-shot이고, pinned runtime slot 서비스(`runtime_topology`)도 아니고 그
      이미지를 쓰는 서비스(generation companion·그 이미지의 one-shot)도 아니어야 한다.

    공용 instance의 db-init 다섯과 Map role bootstrap one-shot이 그 모양이다(S1). admin secret을
    유도할 수 없는 서버는 거부한다 — 규칙을 적용할 수 없는 서버를 조용히 건너뛰면 그것이 곧
    우회로다. **전역 불변식이다. 어떤 서비스의 존재에도 게이팅하지 마라.**
    """

    # pinned runtime slot 서비스는 렌더된 모델과 공용 Dagster plane 스위치에서 파생한다(ADR-54).
    runtime_services = runtime_topology().runtime_services

    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    runtime_images = {
        image
        for name in runtime_services
        if isinstance(runtime := services.get(name), Mapping)
        and isinstance(image := runtime.get("image"), str)
        and image
    }
    for server in sorted(postgres_server_services(document)):
        secret = postgres_admin_secret(document, server)
        password = environment.get(secret.environment, "") if resolved else ""
        for service_name, service in services.items():
            if service_name == server or not isinstance(service, Mapping):
                continue
            if not _service_holds_admin_secret(service, secret, password=password):
                continue
            if (
                service.get("restart") == "no"
                and service_name not in runtime_services
                and service.get("image") not in runtime_images
            ):
                continue
            raise ComposeCandidateContractError(
                "compose candidate hands the admin secret of PostgreSQL instance "
                f"{_describe_candidate_service_key(server)} to a service that is not a "
                "one-shot outside the pinned runtime: "
                + _describe_candidate_service_key(service_name)
            )


#: Map role bootstrap one-shot의 실행 시점 전용 env(ADR-53 S1). admin 이름·포트는 Manager가
#: `run -e`로, password와 bootstrap DSN은 one-shot이 스스로 만든다. compose env에 있으면 안 된다.
MAP_BOOTSTRAP_ADMIN_USER_ENV: Final = "KOR_TRAVEL_MAP_POSTGRES_USER"
MAP_BOOTSTRAP_PORT_ENV: Final = "KTDM_MAP_BOOTSTRAP_PGPORT"
_MAP_DB_ROLE_BOOTSTRAP_RUNTIME_ENV_NAMES: Final = frozenset(
    {
        MAP_BOOTSTRAP_ADMIN_USER_ENV,
        MAP_BOOTSTRAP_PORT_ENV,
        "KOR_TRAVEL_MAP_POSTGRES_PASSWORD",
        "KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN",
    }
)
#: 정본 compose가 이 one-shot에 쓰는 최상위 키 전부(raw·resolved 실측이 같다, 2026-09-29).
#: 허용 목록이다 — `ports`·`user`·`env_file` 같은 키는 자동으로 거부된다.
_MAP_DB_ROLE_BOOTSTRAP_ALLOWED_KEYS: Final = frozenset(
    {
        "command",
        "depends_on",
        "entrypoint",
        "environment",
        "image",
        "network_mode",
        "profiles",
        "restart",
        "secrets",
        "volumes",
    }
)
#: 값이 compose 리터럴인 두 스위치. 나머지 env 키는 계약표(`_MAP_DATABASE_CANONICAL_ENV_VALUES`)의
#: 이 서비스 행에서 온다.
_MAP_DB_ROLE_BOOTSTRAP_LITERAL_ENV_NAMES: Final = frozenset(
    {"KOR_TRAVEL_MAP_DB_ROLE_BOOTSTRAP_ENABLED", "KOR_TRAVEL_MAP_DB_ROLE_BOOTSTRAP_PHASE"}
)
#: `name[:tag]@sha256:<64 hex>` — digest가 이미지를 정한다.
_DIGEST_PINNED_IMAGE: Final = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
#: one-shot이 `exec`하는 Map 스크립트의 파일 이름. 컨테이너 안 경로는 이 이름으로 끝나는
#: source의 bind에서 읽는다(`compose_binds`) — 경로 리터럴은 두지 않는다.
_MAP_DB_ROLE_BOOTSTRAP_SCRIPT: Final = "postgres-role-bootstrap.sh"


def _map_db_role_bootstrap_binds() -> tuple[tuple[str, str], ...]:
    """role bootstrap one-shot의 bind `(raw source, 컨테이너 경로)`, 선언 순서대로.

    정본은 신뢰된 `config/docker-targets.yml`의 `compose_binds` 절이다(GM-17) — 새 bind나 Map
    저장소 기본 경로가 바뀌어도 backend 수정·재설치 없이 그 절과 compose만 바꾼다. 이 one-shot은
    cluster admin secret을 들므로 그 절의 항목은 **모두 읽기 전용**이어야 한다.
    """

    binds = [
        (source, target, read_only)
        for (service, target, read_only), source in (
            registry_module.load_compose_bind_allowlist().items()
        )
        if service == _MAP_DB_ROLE_BOOTSTRAP_SERVICE
    ]
    if not binds or any(read_only is not True for _source, _target, read_only in binds):
        raise ComposeCandidateContractError(
            "Map role bootstrap binds must be declared read-only in compose_binds"
        )
    return tuple((source, target) for source, target, _read_only in binds)


def _map_db_role_bootstrap_script_path() -> str:
    targets = [
        target
        for source, target in _map_db_role_bootstrap_binds()
        if PurePosixPath(source).name == _MAP_DB_ROLE_BOOTSTRAP_SCRIPT
    ]
    if len(targets) != 1:
        raise ComposeCandidateContractError(
            "Map role bootstrap script bind is not derivable from compose_binds"
        )
    return targets[0]


def _resolved_bind_source_matches(
    resolved_source: object, raw_source: str, environment: Mapping[str, str]
) -> bool:
    """resolved 문서의 bind source가 raw source를 같은 env로 보간한 경로인가.

    보간 결과가 절대 경로면 그대로 같아야 한다. 상대 경로(기본값 `../kor-travel-map`)면 compose가
    project 디렉터리에 붙여 풀므로 그 꼬리가 같아야 한다 — 정확한 경로는 모든 bind에 걸리는 볼륨
    그래프 검사(`compose_binds` 대조)가 본다.
    """

    if not isinstance(resolved_source, str) or not resolved_source:
        return False
    expanded = PurePosixPath(posixpath.normpath(_expand_env_path(raw_source, environment)))
    actual = PurePosixPath(posixpath.normpath(resolved_source))
    if expanded.is_absolute():
        return actual == expanded
    tail = tuple(part for part in expanded.parts if part not in {".", ".."})
    return actual.is_absolute() and bool(tail) and actual.parts[-len(tail) :] == tail


def map_db_role_bootstrap_script_lines(secret_target: str) -> tuple[str, ...]:
    """role bootstrap one-shot의 셸 네 줄(ADR-53 S1). ``secret_target``은 그 one-shot의 secret 경로다.

    compose 문서의 모양 그대로다 — `$$`는 compose escape이고, `docker compose config`도 `$$`로
    낸다(n150 Compose v5.2.0 실측). 그래서 raw와 resolved가 같은 네 줄이다. DSN은 컨테이너 셸이
    실행 시점에 만든다 — password는 Manager argv에도 compose env에도 없다. `exec`하는 경로는
    `compose_binds`에서 그 스크립트를 싣는 bind의 컨테이너 경로다.
    """

    return (
        f'KOR_TRAVEL_MAP_POSTGRES_PASSWORD="$$(cat {secret_target})"',
        'KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN="postgresql://'
        "$${KOR_TRAVEL_MAP_POSTGRES_USER}:$${KOR_TRAVEL_MAP_POSTGRES_PASSWORD}"
        "@127.0.0.1:$${KTDM_MAP_BOOTSTRAP_PGPORT}/$${KOR_TRAVEL_MAP_POSTGRES_DB}\"",
        "export KOR_TRAVEL_MAP_POSTGRES_PASSWORD KOR_TRAVEL_MAP_BOOTSTRAP_PG_DSN",
        f"exec /bin/sh {_map_db_role_bootstrap_script_path()}",
    )


def _validate_map_db_role_bootstrap_service(
    service_name: str,
    service: Mapping[str, Any],
    *,
    document: Mapping[str, Any],
    environment: Mapping[str, str],
    resolved: bool,
) -> None:
    """Map role bootstrap one-shot의 실행 표면을 고정한다(ADR-53 S1).

    이 one-shot은 이제 **공용 instance의 admin secret**을 든다 — cluster의 모든 tenant에 닿는
    superuser다. 그래서 무엇을 실행하는지가 전부 계약이다: entrypoint, 셸 네 줄(그 안의 `cat`
    경로는 이 one-shot 자신의 secret 경로), secret 정확히 하나(그 source는 Map DSN 포트의
    instance에서 유도한 admin secret — raw 경로는 `-p`를 보간할 수 없으므로 어떤 서버의 admin
    secret이든), `compose_binds`의 그 서비스 항목과 원소 단위로 같은 `:ro` mount, profile·restart,
    허용 목록 밖의 키 없음, digest로 고정한 이미지, 실행 시점 전용 env 네 이름이 compose env에
    없음, 계약표 밖의 env 키 없음.
    """

    if service_name != _MAP_DB_ROLE_BOOTSTRAP_SERVICE:
        raise ComposeCandidateContractError("Map role bootstrap service identity is invalid")
    if set(service) - _MAP_DB_ROLE_BOOTSTRAP_ALLOWED_KEYS:
        raise ComposeCandidateContractError(
            "Map role bootstrap service declares keys outside its contract: "
            + ", ".join(sorted(str(key) for key in set(service) - _MAP_DB_ROLE_BOOTSTRAP_ALLOWED_KEYS))
        )
    if service.get("entrypoint") != ["/bin/sh", "-ec"]:
        raise ComposeCandidateContractError("Map role bootstrap service entrypoint is invalid")
    if service.get("restart") != "no" or service.get("profiles") != ["bootstrap"]:
        raise ComposeCandidateContractError("Map role bootstrap service lifecycle is invalid")
    references = service.get("secrets")
    mount = (
        _secret_reference_mount(references[0])
        if isinstance(references, list) and len(references) == 1
        else None
    )
    if mount is None:
        raise ComposeCandidateContractError(
            "Map role bootstrap service must mount exactly one secret"
        )
    source, target = mount
    if resolved:
        authority = loopback_dsn_authority(environment.get("KOR_TRAVEL_MAP_PG_DSN"))
        instances = (
            postgres_server_services_on_port(document, authority[1])
            if authority is not None
            else ()
        )
        if len(instances) != 1:
            raise ComposeCandidateContractError(
                "Map role bootstrap service instance is not derivable from the Map DSN port"
            )
        admin_sources = {postgres_admin_secret(document, instances[0]).source}
    else:
        admin_sources = {
            postgres_admin_secret(document, server).source
            for server in postgres_server_services(document)
        }
    if source not in admin_sources:
        raise ComposeCandidateContractError(
            "Map role bootstrap service must mount the admin secret of the instance on the "
            "Map DSN port"
        )
    command = service.get("command")
    if (
        not isinstance(command, list)
        or len(command) != 1
        or not isinstance(command[0], str)
        or tuple(line.strip() for line in command[0].strip().splitlines())
        != map_db_role_bootstrap_script_lines(target)
    ):
        raise ComposeCandidateContractError("Map role bootstrap service command is invalid")
    # mount는 `compose_binds`의 그 서비스 항목과 **원소 단위로** 같아야 한다(순서·개수 포함,
    # 전부 읽기 전용). raw는 원문 그대로, resolved는 보간한 source와 `type: bind`로 본다.
    binds = _map_db_role_bootstrap_binds()
    volumes = service.get("volumes")
    if not isinstance(volumes, list) or len(volumes) != len(binds):
        raise ComposeCandidateContractError("Map role bootstrap service mounts are invalid")
    for volume, (bind_source, container_path) in zip(volumes, binds, strict=True):
        if resolved:
            valid = (
                isinstance(volume, Mapping)
                and volume.get("type") == "bind"
                and volume.get("read_only") is True
                and volume.get("target") == container_path
                and _resolved_bind_source_matches(volume.get("source"), bind_source, environment)
            )
        else:
            valid = volume == f"{bind_source}:{container_path}:ro"
        if not valid:
            raise ComposeCandidateContractError("Map role bootstrap service mounts are invalid")
    # 그 admin secret을 받는 `/bin/sh`·`psql`이 무엇인지도 계약이다 — 태그만 두면 `docker pull`
    # 한 번이 cluster admin 자격증명을 보는 바이너리를 바꾼다(공용 서버 이미지를 digest로 고정한
    # 것과 같은 이유).
    image = service.get("image")
    if not isinstance(image, str) or _DIGEST_PINNED_IMAGE.fullmatch(image) is None:
        raise ComposeCandidateContractError(
            "Map role bootstrap service image must be pinned by digest"
        )
    environment_names = {name for name, _value in _service_environment_items(service)}
    runtime_names = sorted(environment_names & _MAP_DB_ROLE_BOOTSTRAP_RUNTIME_ENV_NAMES)
    if runtime_names:
        raise ComposeCandidateContractError(
            "Map role bootstrap service environment carries run-time values: "
            + ", ".join(runtime_names)
        )
    # env 키도 허용 목록이다 — 계약표(`_MAP_DATABASE_CANONICAL_ENV_VALUES`)의 이 서비스 행과 값이
    # 리터럴인 두 스위치뿐이다. `PGOPTIONS`·`PSQL*` 같은 키 하나가 Map password를 정하는
    # superuser 세션을 바꿀 수 있다.
    allowed_names = {
        name
        for service_key, name in _MAP_DATABASE_CANONICAL_ENV_VALUES
        if service_key == _MAP_DB_ROLE_BOOTSTRAP_SERVICE
    } | _MAP_DB_ROLE_BOOTSTRAP_LITERAL_ENV_NAMES
    unexpected = sorted(environment_names - allowed_names)
    if unexpected:
        raise ComposeCandidateContractError(
            "Map role bootstrap service environment declares keys outside its contract: "
            + ", ".join(unexpected)
        )


_ISO8601_DATETIME_WITH_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:[Zz]|[+-]\d{2}:?\d{2})$"
)
_OPERATION_STATES = frozenset({"queued", "running", "done", "failed", "cancelled"})
_PROVIDER_SYNC_STATUSES = frozenset(
    {"active", "paused", "disabled", "failed", "never_run"}
)
_RETRYABLE_CANCELLATION_ERROR_CODES = frozenset(
    {
        "DAGSTER_TERMINATE_FAILED",
        "DAGSTER_TERMINATION_TIMEOUT",
        "DAGSTER_UNAVAILABLE",
    }
)
_FAILED_CANCELLATION_ERROR_CODES = frozenset(
    {
        "DAGSTER_RECONCILE_FAILED",
        "PIPELINE_CANCELLATION_INVARIANT",
        "PIPELINE_CANCELLATION_UNSAFE",
    }
)
_LEGACY_PAIR_MANIFEST_FILENAME = "compatible-pair-v4.json"
_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_CONTRACT_GENERATION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_COMPOSE_PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
_ASCII_RETRY_AFTER = re.compile(r"^[0-9]+$")
_PINVI_LOGIN_PAGE_CHUNK_PATTERN = re.compile(
    r'<script\b[^>]*\bsrc=["\']'
    r"/_next/static/chunks/app/\(admin\)/admin/login/page-[0-9a-f]+\.js"
    r'(?:\?[^"\']*)?["\'][^>]*>',
    re.IGNORECASE,
)
_MAP_UI_PASSWORD_HASH_PATTERN = re.compile(
    r"^pbkdf2_sha256\$([0-9]+)\$[0-9A-Za-z_-]+\$[0-9A-Za-z_-]+$"
)
_CANDIDATE_EXTERNAL_FILE_MAX_BYTES = 1_048_576
_CANDIDATE_ALLOWED_EXTERNAL_RESOURCE_REFERENCES: frozenset[
    tuple[str, str, str]
] = frozenset()
_CANDIDATE_ALLOWED_SYSTEM_BINDS = {
    ("cadvisor", "/sys", True): "/sys",
    ("cadvisor", "/var/run/docker.sock", True): "/var/run/docker.sock",
}
#: operator bind의 host source가 **절대 될 수 없는** 자리. GM-17 A 적대 리뷰가
#: 찾은 구멍이다 — allowlist가 설정으로 나온 뒤 `source: "/etc"` 한 줄이면 production
#: 컨테이너가 host `/etc`를 쓰기 가능으로 얻는다. 종전 manager 가드는 manager 파일의
#: **조상**만 거부해서(`resolved_source in manager_path.parents`) `/etc`·`/root`처럼
#: 조상이 아닌 민감 디렉터리를 막지 못했고, 디렉터리는 내용 스캔도 받지 않는다.
#:
#: 여기 있는 것과 그 **하위 전부**를 거부한다. system bind(cadvisor `/sys`,
#: docker.sock)는 이 검사 앞에서 `continue`하므로 영향받지 않는다.
_CANDIDATE_FORBIDDEN_OPERATOR_BIND_SOURCES: Final[tuple[str, ...]] = (
    "/etc",
    "/root",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
    "/var/lib/docker",
    "/var/run/docker.sock",
)

#: 경로 어디에든 이 이름이 있으면 거부한다. `/home/*/.ssh`를 경로 리터럴로 열거할 수
#: 없기 때문이다.
_CANDIDATE_FORBIDDEN_BIND_SOURCE_COMPONENTS: Final[frozenset[str]] = frozenset(
    {".ssh", ".gnupg", ".aws", ".kube"}
)


def _assert_operator_bind_source_is_permitted(*, service: str, resolved_source: Path) -> None:
    """operator bind의 host source가 허용된 자리인가.

    **이 검사는 allowlist가 설정으로 나오면서 필요해졌다.** 종전에는 목록이 코드
    상수라 새 source를 넣으려면 backend를 고치고 재설치해야 했고, 그 과정 자체가
    리뷰였다. 이제는 설정 한 줄이므로 목록에 무엇이 들어올 수 있는지를 코드가 말해야
    한다.

    두 부류를 막는다.

    1. **민감한 host 자리.** `/etc`·`/root` 같은 디렉터리는 manager 파일의 조상이
       아니라서 종전 가드를 그냥 지나갔고, 디렉터리 bind는 protected 값 스캔도 받지
       않는다(`S_ISDIR`이면 내용 검사가 없다). 즉 아무 신호 없이 통과했다.
    2. **자기-인가 루프.** allowlist 파일 자신과 backend 소스를 bind source로 쓰면,
       그 컨테이너가 다음 재기동에 임의 bind를 인가할 수 있다. 인가하는 것과 인가되는
       것이 같아지면 경계가 아니다. 설치 트리 전체를 막지는 **않는다** —
       `./config/prometheus/...`·`./scripts/...`가 정당하게 그 안에 있다.
    """

    for forbidden in _CANDIDATE_FORBIDDEN_OPERATOR_BIND_SOURCES:
        denied = Path(forbidden)
        if resolved_source == denied or denied in resolved_source.parents:
            raise ComposeCandidateContractError(
                f"compose candidate {service} bind source is a forbidden host location: "
                f"{resolved_source}"
            )
    if _CANDIDATE_FORBIDDEN_BIND_SOURCE_COMPONENTS.intersection(resolved_source.parts):
        raise ComposeCandidateContractError(
            f"compose candidate {service} bind source exposes a credential directory: "
            f"{resolved_source}"
        )

    targets_config = Path(get_targets_config_path()).resolve()
    if resolved_source == targets_config:
        raise ComposeCandidateContractError(
            f"compose candidate {service} bind source is the bind allowlist itself — "
            "인가하는 파일과 인가되는 것이 같아지면 경계가 아니다"
        )
    # `resolved_source`는 풀린 경로다. 설치 root는 release symlink이므로 이쪽도 풀어야
    # 비교가 성립한다 — 안 풀면 가드가 조용히 지나간다(ADR-51 D).
    backend_root = TRUSTED_INSTALL_ROOT.resolve() / "backend"
    if resolved_source == backend_root or backend_root in resolved_source.parents:
        raise ComposeCandidateContractError(
            f"compose candidate {service} bind source exposes manager backend source: "
            f"{resolved_source}"
        )


# GM-17 본작업 A: 허용 bind 목록의 정본은 `config/docker-targets.yml`의
# `compose_binds:` 절이다. 종전에는 여기 125줄짜리 dict 리터럴이었고, 그래서 새 bind
# 하나 또는 여섯 번째 프로젝트의 pgdata에도 backend 수정 + trusted release 재설치가
# 필요했다. 값은 한 글자도 바꾸지 않고 자리만 옮겼다 — 옮기기 전후의 해석된 매핑이
# 정확히 같다는 것을 `tests/test_registry_targets_config.py`가 결박한다.
#
# 그 문서를 신뢰할 수 있게 만든 것이 선행조건이었다(`registry.get_targets_config_path`/
# `_read_targets_bytes`): trusted 설치본에서 env redirect 거부 + root 소유·비쓰기 강제.
# 그것 없이 옮겼다면 이 이관 자체가 보안 회귀였다.
_CANDIDATE_ALLOWED_EXTERNAL_VOLUME_REFERENCES: frozenset[str] = frozenset()
_HELD_DEPLOYMENT_LOCKS: ContextVar[frozenset[str]] = ContextVar(
    "held_c6c_deployment_locks", default=frozenset()
)
_PINNED_REBUILD_INHERITED_GLOBAL_LOCK_FD_ENV = GLOBAL_MUTATION_LOCK_FD_ENV


@dataclass(frozen=True)
class CandidatePathIdentity:
    path: str
    device: int
    inode: int
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class CandidateSystemBindSnapshot:
    service: str
    source: str
    target: str
    read_only: bool
    path_chain: tuple[CandidatePathIdentity, ...]


@dataclass(frozen=True)
class CandidateVolumeMount:
    kind: str
    source: str
    target: str
    read_only: bool
    declared_source: str | None = None
    declared_target: str | None = None


@dataclass(frozen=True)
class C6cSmokeConfig:
    pinvi_api_base_url: str
    map_ui_base_url: str
    pinvi_web_base_url: str
    map_ui_username: str
    map_ui_password: str = field(repr=False)
    pinvi_admin_email: str = field(repr=False)
    pinvi_admin_password: str = field(repr=False)


@dataclass(frozen=True)
class C6cDeploymentConfig:
    deployment_environment: str
    pinvi_environment: str
    base_url: str
    map_container_port: int
    read_token: str = field(repr=False)
    cancel_token: str = field(repr=False)
    fixture_token: str = field(repr=False)
    map_container: str
    map_ui_container: str
    map_ui_password_hash: str = field(repr=False)
    map_ui_session_secret: str = field(repr=False)
    map_admin_proxy_secret: str = field(repr=False)
    map_service_token: str = field(repr=False)
    map_cursor_signing_secret: str = field(repr=False)
    map_geo_api_key: str = field(repr=False)
    pinvi_container: str
    contract_generation: str = field(repr=False)
    smoke: C6cSmokeConfig
    curation_snapshot_token: str = field(default="", repr=False)
    curation_cutover_mapping_token: str = field(default="", repr=False)
    feature_create_token: str = field(default="", repr=False)
    feature_create_token_digest: str = field(default="", repr=False)
    feature_create_enabled: str = field(default="false", repr=False)

    @property
    def production(self) -> bool:
        return self.deployment_environment == "production"


@dataclass(frozen=True)
class C6cBuildProvenance:
    map_source_revision: str
    pinvi_source_revision: str

    def compose_environment(self) -> dict[str, str]:
        return {
            "KOR_TRAVEL_MAP_GIT_COMMIT": self.map_source_revision,
            "PINVI_SOURCE_REVISION": self.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        }

@dataclass(frozen=True)
class HttpProbeResponse:
    status: int
    payload: Any | None
    retry_after: int | None = None
    retry_after_present: bool | None = None
    set_cookie: bool = False
    location: str | None = None
    body_text: str | None = None
    content_type: str | None = None


@dataclass
class PinviCancelProbeState:
    """한 runtime generation transaction의 C6c fixture cancel 상태.

    pinned runtime 배포에서 ``transaction_id``는 그 배포의 ``deploy-status.json``
    ``run_id``다. 기본값은 단위 검증과 non-F1D caller의 안전한 일회성 상태를 위한
    것이다. 재개 경로가 없으므로 attempted high-watermark는 한 실행 안에서만 산다.
    """

    transaction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fixture: C6cCancelProbeFixture | None = None
    attempted: bool = False
    finalize_attempted: bool = False
    result: dict[str, int | str] | None = None


@dataclass(frozen=True)
class C6cCancelProbeFixture:
    """Map fixture lifecycle API가 반환하는 secret-free durable 상태."""

    transaction_id: str
    job_id: str
    state: Literal["armed", "consumed", "finalized"]
    cancellation_id: str | None
    canonical_unsafe_outcome: dict[str, int | str] | None
    created_at: str | None = None
    consumed_at: str | None = None
    finalized_at: str | None = None


def assert_manager_mutation_allowed(
    *,
    env_path: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """모든 manager mutation이 공유하는 명시적 실행 환경 계약을 검증한다."""

    if environment is None:
        if env_path is None:
            raise DeploymentContractError(
                "manager mutation requires a frozen environment"
            )
        environment = effective_environment(env_path)
    return _validate_mutation_environment(environment)


def assert_compose_mutation_allowed(
    identifiers: Iterable[str],
    *,
    env_path: str | None = None,
    environment: Mapping[str, str] | None = None,
    capability: object | None = None,
) -> None:
    """production low-level Compose mutation은 신뢰된 상위 workflow만 호출한다."""

    normalized = {str(identifier).strip() for identifier in identifiers}
    if not normalized:
        return
    if capability is _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY:
        assert_pinned_runtime_rebuild_allowed(
            env_path=env_path, environment=environment
        )
        return
    mode = assert_manager_mutation_allowed(
        env_path=env_path,
        environment=environment,
    )
    if mode == "production" and capability is not _MANAGED_COMPOSE_MUTATION_CAPABILITY:
        raise DeploymentContractError(
            "production Compose mutation requires a managed workflow capability"
        )


def assert_pinned_runtime_rebuild_allowed(
    *,
    env_path: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    """v5의 파기형 단일-active rebuild에만 별도 mutation capability를 준다."""

    values = environment
    if values is None:
        if env_path is None:
            raise DeploymentContractError(
                "pinned runtime rebuild requires a frozen environment"
            )
        values = effective_environment(env_path)
    required = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        _MAP_REQUIRED_ENV: "true",
    }
    if any(values.get(key, "").strip().lower() != expected for key, expected in required.items()):
        raise DeploymentContractError(
            "pinned runtime rebuild requires rehearsal/rebuildable environment"
        )


def _validate_mutation_environment(values: Mapping[str, str]) -> str:
    """모든 managed mutation 진입점이 공유하는 최소 fail-close 환경 계약."""

    deployment_environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    pinvi_environment = values.get("PINVI_ENVIRONMENT", "").strip().lower()
    if deployment_environment not in {"local", "production"}:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must be explicitly set before manager mutation"
        )
    if pinvi_environment not in {"development", "production"}:
        raise DeploymentContractError(
            "PINVI_ENVIRONMENT must be explicitly set before manager mutation"
        )
    expected_pinvi_environment = (
        "production" if deployment_environment == "production" else "development"
    )
    if pinvi_environment != expected_pinvi_environment:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must map local->development and "
            "production->production for PINVI_ENVIRONMENT"
        )

    required_text = values.get(_MAP_REQUIRED_ENV, "").strip().lower()
    expected_required = "true" if deployment_environment == "production" else "false"
    if required_text != expected_required:
        raise DeploymentContractError(
            f"{_MAP_REQUIRED_ENV} must be explicitly set to {expected_required} "
            "before manager mutation"
        )
    _validate_raw_token_pair(
        values.get(_MAP_READ_ENV, ""),
        values.get(_MAP_CANCEL_ENV, ""),
        values.get(_MAP_FIXTURE_ENV, ""),
        require_nonempty=deployment_environment == "production",
    )
    return deployment_environment


@contextmanager
def c6c_deployment_lock(path: str) -> Iterator[None]:
    """배포 preflight부터 manifest commit/복구까지 host-wide nonblocking lock."""

    lock_path = Path(path)
    lock_key = str(lock_path if lock_path.is_absolute() else lock_path.absolute())
    held_locks = _HELD_DEPLOYMENT_LOCKS.get()
    if lock_key in held_locks:
        yield
        return
    inherited_fd = _verified_inherited_global_mutation_lock_fd(lock_path)
    if inherited_fd is not None:
        context_token = _HELD_DEPLOYMENT_LOCKS.set(held_locks | {lock_key})
        try:
            yield
        finally:
            _HELD_DEPLOYMENT_LOCKS.reset(context_token)
        return
    _prepare_c6c_lock_directory(lock_path.parent)
    fd: int | None = None
    context_token = None
    production_lease = lock_path == _C6C_GLOBAL_MUTATION_LOCK
    try:
        try:
            flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(lock_path, flags, 0o600)
            _validate_c6c_lock_fd(fd, production=production_lease)
        except OSError as exc:
            raise DeploymentContractError("cannot acquire C6c deployment lock") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            # 기다리지 않는다. 경합 하나만 전용 코드로 구분한다 — 안전하지 않은 lock은
            # 위아래의 일반 DeploymentContractError로 남는다.
            raise ManagerMutationActiveError(
                "another Manager mutation is already active; nothing was changed"
            ) from exc
        _assert_locked_fd_still_owns_path(fd, lock_path)
        # 소유권·mode 계약도 다시 본다. 신원(inode)만 재대조하면, 잠그는 사이에 그
        # inode가 world-writable로 바뀌어도 통과한다.
        _validate_c6c_lock_fd(fd, production=production_lease)
        context_token = _HELD_DEPLOYMENT_LOCKS.set(held_locks | {lock_key})
        yield
    finally:
        if context_token is not None:
            _HELD_DEPLOYMENT_LOCKS.reset(context_token)
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _verified_inherited_global_mutation_lock_fd(lock_path: Path) -> int | None:
    """one-shot launcher가 보유한 production lock FD만 안전하게 재사용한다."""

    if lock_path != _C6C_GLOBAL_MUTATION_LOCK:
        return None
    raw_fd = os.environ.get(_PINNED_REBUILD_INHERITED_GLOBAL_LOCK_FD_ENV, "")
    if not raw_fd:
        return None
    if not raw_fd.isdecimal():
        raise DeploymentContractError("inherited C6c deployment lock descriptor is invalid")
    try:
        fd = int(raw_fd)
        descriptor_stat = os.fstat(fd)
        path_stat = lock_path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "inherited C6c deployment lock descriptor is unavailable"
        ) from exc
    if (
        (descriptor_stat.st_dev, descriptor_stat.st_ino)
        != (path_stat.st_dev, path_stat.st_ino)
    ):
        raise DeploymentContractError("inherited C6c deployment lock descriptor is unsafe")
    _validate_c6c_lock_fd(fd, production=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise DeploymentContractError(
            "inherited C6c deployment lock is not held"
        ) from exc
    return fd


def manager_mutation_lock() -> AbstractContextManager[None]:
    """host 변경 lock ``G`` 하나를 잡는 유일한 획득 경로(ADR-51 C).

    CLI pin mutator와 pinned rebuild가 같은 이 함수를 지난다. lock 파일이 없으면 먼저
    온 쪽이 ``O_CREAT|O_NOFOLLOW`` ``0600``으로 만들어 잡는다 — lock 없이 진행하는
    경로는 없다. 경합이면 기다리지 않고 ``ManagerMutationActiveError``로 거절한다.
    경로는 호출 시점에 읽는다(테스트가 모듈 상수를 tmp로 옮길 수 있도록).
    """

    return c6c_deployment_lock(str(_C6C_GLOBAL_MUTATION_LOCK))


def _prepare_c6c_lock_directory(path: Path) -> None:
    if path == _C6C_GLOBAL_MUTATION_LOCK.parent and os.geteuid() != _GLOBAL_LOCK_OWNER_UID:
        raise DeploymentContractError("the Manager mutation lock requires root")
    # `/run/lock`은 `1777` sticky다. 런타임 최초 생성은 그래서 선점 창이고, 이 창은
    # 코드로 닫히지 않는다 — 부팅 시점에 이미 존재하게 만드는 것만이 닫는다
    # (`deploy/tmpfiles.d/kor-travel-docker-manager.conf`). 아래 `mkdir`은 그 유닛이
    # 설치되지 않은 호스트를 위한 폴백이며, 창을 없애지 못한다.
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path == _C6C_GLOBAL_MUTATION_LOCK.parent:
        st = path.lstat()
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != _GLOBAL_LOCK_OWNER_UID
            or stat.S_IMODE(st.st_mode) != 0o700
        ):
            raise DeploymentContractError("production C6c deployment lock directory is unsafe")


def _assert_locked_fd_still_owns_path(fd: int, lock_path: Path) -> None:
    """flock을 잡은 inode가 지금도 그 경로의 inode인지 확인한다.

    ``flock``은 경로가 아니라 inode 단위다. 우리가 열고 잠근 사이에 누군가 그 경로를
    unlink하고 새 파일을 만들었다면, 두 주체가 서로 다른 inode를 잠근 채 자기가
    상호배제를 얻었다고 믿게 된다. ``_verified_inherited_global_mutation_lock_fd``는
    이미 같은 대조를 한다.

    **창을 좁힐 뿐 닫지는 못한다.** 두 주체가 각자 자기 시점에 통과한 뒤 서로 다른
    inode를 들고 있는 상태는 시점 검사로 잡히지 않는다. 상호배제는 여전히 lease
    디렉터리에 아무도 쓸 수 없다는 전제(`0700 root:root`) 위에 성립한다. 그 전제가
    코드 밖에 있으므로, 깨졌을 때 조용히 통과하지는 않게 해 두는 것이 목적이다.
    """

    descriptor = os.fstat(fd)
    try:
        current = lock_path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "C6c deployment lock vanished after acquisition"
        ) from exc
    if (descriptor.st_dev, descriptor.st_ino) != (current.st_dev, current.st_ino):
        raise DeploymentContractError("C6c deployment lock was replaced during acquisition")


def _validate_c6c_lock_fd(fd: int, *, production: bool) -> None:
    st = os.fstat(fd)
    expected_uid = _GLOBAL_LOCK_OWNER_UID if production else os.geteuid()
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or st.st_uid != expected_uid
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        raise DeploymentContractError("C6c deployment lock is unsafe")


def effective_environment(env_path: str) -> dict[str, str]:
    """Compose와 같은 우선순위로 env-file 위에 process env를 겹친다."""

    values: dict[str, str] = {}
    if os.path.exists(env_path):
        values.update(
            {
                key: value or ""
                for key, value in dotenv_values(env_path).items()
                if isinstance(key, str)
            }
        )
    values.update(os.environ)
    return derive_curation_service_principal_environment(values)


def c6c_state_paths(values: Mapping[str, str]) -> tuple[str, str]:
    """legacy v4 tombstone 경로와 host-global lock을 함께 정한다.

    첫 경로는 legacy artifact 탐지에만 남아 있으며 현재 배포 기록(``deploy-status.json``)은
    이를 읽거나 쓰지 않는다. 두 번째 lock 경로만 현재 Manager mutation
    serialization에 사용한다.
    """

    production = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower() == "production"
    project_name = values.get("COMPOSE_PROJECT_NAME", "").strip().lower()
    if not project_name and not production:
        project_name = "kor-travel-local"
    if not _COMPOSE_PROJECT_PATTERN.fullmatch(project_name):
        raise DeploymentContractError(
            "COMPOSE_PROJECT_NAME must be explicit and canonical for C6c state"
        )
    configured_root = values.get("KTDM_C6C_STATE_ROOT", "").strip()
    if production:
        if configured_root:
            raise DeploymentContractError("production C6c state root path is fixed")
        root = _C6C_PRODUCTION_STATE_ROOT
    else:
        default_root = Path.home() / ".local" / "state" / "kor-travel-docker-manager"
        root = _canonical_absolute_path(
            configured_root or str(default_root),
            "KTDM_C6C_STATE_ROOT",
        )
    state_dir = _canonical_absolute_path(
        str(root / project_name),
        "C6c deployment state directory",
    )
    manifest_override = values.get("KTDM_C6C_COMPATIBLE_PAIR_MANIFEST", "").strip()
    if production and manifest_override:
        raise DeploymentContractError("production C6c manifest path is fixed")
    legacy_artifact = _canonical_absolute_path(
        manifest_override or str(state_dir / _LEGACY_PAIR_MANIFEST_FILENAME),
        "KTDM_C6C_COMPATIBLE_PAIR_MANIFEST",
    )
    lock = Path(manager_mutation_lock_path(values))
    if legacy_artifact == lock:
        raise DeploymentContractError("C6c manifest and lock paths must differ")
    return str(legacy_artifact), str(lock)


def ensure_c6c_state_directory(path: str | Path) -> None:
    """C6c state directory를 production/local 모두 owner-only 정책으로 준비한다."""

    target = Path(path)
    if _is_relative_to(target.resolve(strict=False), _C6C_PRODUCTION_STATE_ROOT):
        expected_uid = _production_state_owner_uid()
        _ensure_single_c6c_state_directory(
            _C6C_PRODUCTION_STATE_ROOT,
            expected_uid=expected_uid,
        )
        current = _C6C_PRODUCTION_STATE_ROOT
        for part in target.resolve(strict=False).relative_to(_C6C_PRODUCTION_STATE_ROOT).parts:
            current = current / part
            _ensure_single_c6c_state_directory(current, expected_uid=expected_uid)
        return
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_c6c_state_directory(target, expected_uid=os.geteuid())


def _ensure_single_c6c_state_directory(path: Path, *, expected_uid: int) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise DeploymentContractError("C6c state directory is unavailable") from exc
    directory_stat = _c6c_state_directory_stat(path, expected_uid=expected_uid)
    if stat.S_IMODE(directory_stat.st_mode) != 0o700:
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise DeploymentContractError("C6c state directory mode cannot be fixed") from exc
        _validate_c6c_state_directory(path, expected_uid=expected_uid)


def _production_state_owner_uid() -> int:
    if _C6C_PRODUCTION_STATE_ROOT == _DEFAULT_C6C_PRODUCTION_STATE_ROOT:
        return 0
    return os.geteuid()


def _c6c_state_directory_stat(path: Path, *, expected_uid: int) -> os.stat_result:
    try:
        directory_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("C6c state directory is unavailable") from exc
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != expected_uid
    ):
        raise DeploymentContractError("C6c state directory is unsafe")
    return directory_stat


def _validate_c6c_state_directory(path: Path, *, expected_uid: int) -> None:
    directory_stat = _c6c_state_directory_stat(path, expected_uid=expected_uid)
    if (
        stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise DeploymentContractError("C6c state directory is unsafe")


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def manager_mutation_lock_path(values: Mapping[str, str]) -> str:
    """주어진 ``.env`` 값이 가리키는 Manager 변경 lock 경로(ADR-51 C-3).

    ``local``만 비root 개발용 실행 사용자 ``$HOME`` 아래 lock이다. 그 밖의 모든 값 —
    production·rehearsal뿐 아니라 미지정·미지의 모드도 — 은 host 변경 lock ``G``다
    (fail closed: 모드를 빠뜨린 root 호스트가 launcher·pin 회전·installer와 갈라지지
    않는다). 경로 override는 없다. 값은 호출자가 넘긴 매핑에서만 읽는다 — 프로세스
    환경으로 채우지 않는다(ADR-41 교훈: 신뢰 결정의 입력을 프로세스 env에서 읽지 않는다).
    """

    mode = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    if mode != "local":
        return str(_C6C_GLOBAL_MUTATION_LOCK)
    return str(
        (
            Path.home()
            / ".local"
            / "state"
            / "kor-travel-docker-manager"
            / "global-mutation.lock"
        ).resolve(strict=False)
    )


def _canonical_absolute_path(value: str, env_name: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=False):
        raise DeploymentContractError(f"{env_name} must be a canonical absolute path")
    return path


def load_c6c_deployment_config(env_path: str) -> C6cDeploymentConfig:
    return load_c6c_deployment_config_from_environment(
        effective_environment(env_path)
    )


def load_c6c_deployment_config_from_environment(
    environment: Mapping[str, str],
) -> C6cDeploymentConfig:
    values = derive_curation_service_principal_environment(environment)
    deployment_environment = values.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower()
    pinvi_environment = values.get("PINVI_ENVIRONMENT", "").strip().lower()

    if deployment_environment not in {"local", "rehearsal", "production"}:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must be explicitly set to local, rehearsal, or production"
        )
    if pinvi_environment not in {"development", "production"}:
        raise DeploymentContractError(
            "PINVI_ENVIRONMENT must be explicitly set to development or production"
        )
    expected_pinvi_environment = (
        "production"
        if deployment_environment in {"rehearsal", "production"}
        else "development"
    )
    if pinvi_environment != expected_pinvi_environment:
        raise DeploymentContractError(
            "KTDM_DEPLOYMENT_ENVIRONMENT must map local->development and "
            "production->production for PINVI_ENVIRONMENT"
        )

    required_text = values.get(_MAP_REQUIRED_ENV, "").strip().lower()
    expected_required = (
        "true" if deployment_environment in {"rehearsal", "production"} else "false"
    )
    if required_text != expected_required:
        raise DeploymentContractError(
            f"{_MAP_REQUIRED_ENV} must be explicitly set to {expected_required} "
            f"for {deployment_environment}"
        )

    map_container_port = _parse_port(
        values.get("KOR_TRAVEL_MAP_API_CONTAINER_PORT", "12701"),
        "KOR_TRAVEL_MAP_API_CONTAINER_PORT",
    )
    pinvi_api_port = _parse_port(values.get("PINVI_API_PORT", "12801"), "PINVI_API_PORT")
    map_ui_port = _parse_port(
        values.get("KOR_TRAVEL_MAP_UI_PORT", "12705"),
        "KOR_TRAVEL_MAP_UI_PORT",
    )
    pinvi_web_port = _parse_port(values.get("PINVI_WEB_PORT", "12805"), "PINVI_WEB_PORT")

    base_url = values.get(
        "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL",
        f"http://127.0.0.1:{map_container_port}",
    )
    contract_generation = values.get("KTDM_C6C_CONTRACT_GENERATION", "").strip().lower()
    if not isinstance(contract_generation, str) or not _CONTRACT_GENERATION_PATTERN.fullmatch(
        contract_generation
    ):
        raise DeploymentContractError(
            "KTDM_C6C_CONTRACT_GENERATION must be an explicit stable identifier"
        )
    if not _map_ui_auth_values_are_valid(values):
        raise DeploymentContractError(
            "Map UI runtime authentication environment is invalid"
        )
    feature_create_enabled = values.get(_MAP_FEATURE_CREATE_ENABLED_ENV, "false")

    config = C6cDeploymentConfig(
        deployment_environment=deployment_environment,
        pinvi_environment=pinvi_environment,
        base_url=base_url,
        map_container_port=map_container_port,
        read_token=values.get(_MAP_READ_ENV, ""),
        cancel_token=values.get(_MAP_CANCEL_ENV, ""),
        fixture_token=values.get(_MAP_FIXTURE_ENV, ""),
        map_container=values.get("KOR_TRAVEL_MAP_API_CONTAINER", "kor-travel-map-api-latest"),
        map_ui_container=values.get(
            "KOR_TRAVEL_MAP_UI_CONTAINER", "kor-travel-map-ui-latest"
        ),
        map_ui_password_hash=values.get(_MAP_UI_PASSWORD_HASH_ENV, ""),
        map_ui_session_secret=values.get(_MAP_UI_SESSION_SECRET_ENV, ""),
        map_admin_proxy_secret=values.get(_MAP_ADMIN_PROXY_ENV, ""),
        map_service_token=values.get(_MAP_SERVICE_TOKEN_ENV, ""),
        map_cursor_signing_secret=values.get(_MAP_CURSOR_SIGNING_SECRET_ENV, ""),
        map_geo_api_key=values.get(_MAP_GEO_API_KEY_SOURCE_ENV, ""),
        pinvi_container=values.get("PINVI_API_CONTAINER", "pinvi-api-latest"),
        contract_generation=contract_generation,
        smoke=C6cSmokeConfig(
            pinvi_api_base_url=f"http://127.0.0.1:{pinvi_api_port}",
            map_ui_base_url=f"http://127.0.0.1:{map_ui_port}",
            pinvi_web_base_url=f"http://127.0.0.1:{pinvi_web_port}",
            map_ui_username=values.get(_MAP_UI_USERNAME_ENV, ""),
            map_ui_password=values.get(_MAP_UI_PASSWORD_ENV, ""),
            pinvi_admin_email=values.get("KTDM_C6C_PINVI_ADMIN_EMAIL", ""),
            pinvi_admin_password=values.get(_PINVI_ADMIN_PASSWORD_ENV, ""),
        ),
        curation_snapshot_token=values.get(_PINVI_CURATION_SNAPSHOT_ENV, ""),
        curation_cutover_mapping_token=values.get(
            _PINVI_CUTOVER_MAPPING_ENV,
            "",
        ),
        feature_create_token=values.get(_MAP_FEATURE_CREATE_TOKEN_ENV, ""),
        feature_create_token_digest=values.get(
            _MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV, ""
        ),
        feature_create_enabled=feature_create_enabled,
    )
    validate_c6c_operation_tokens(
        values,
        require_nonempty=deployment_environment in {"rehearsal", "production"},
    )
    _validate_map_production_secrets(config)
    if config.production:
        c6c_state_paths(values)
        _validate_production_config(config, values)
    return config


def _map_ui_auth_values_are_valid(values: Mapping[str, str]) -> bool:
    username = values.get(_MAP_UI_USERNAME_ENV, "")
    password_hash = values.get(_MAP_UI_PASSWORD_HASH_ENV, "")
    session_secret = values.get(_MAP_UI_SESSION_SECRET_ENV, "")
    if not all(
        isinstance(value, str) for value in (username, password_hash, session_secret)
    ):
        return False
    if (
        not username
        or username != username.strip()
        or "\r" in username
        or "\n" in username
    ):
        return False
    if not is_pbkdf2_sha256_password_hash(password_hash):
        return False
    return len(session_secret) >= 32 and not any(
        character.isspace() for character in session_secret
    )


def is_pbkdf2_sha256_password_hash(value: str) -> bool:
    """운영 UI가 수용하는 PBKDF2-SHA256 hash 형식을 값 비노출으로 판별한다."""

    match = _MAP_UI_PASSWORD_HASH_PATTERN.fullmatch(value)
    if match is None:
        return False
    try:
        return int(match.group(1)) >= 100_000
    except ValueError:
        return False


def _concierge_ui_root_values_are_valid(values: Mapping[str, str]) -> bool:
    """Concierge UI에 명시적으로 허용한 Manager root 값의 형식을 고정한다."""

    required = {
        name: values.get(name, "") for name in _CONCIERGE_UI_REQUIRED_ROOT_ENV_NAMES
    }
    if not all(isinstance(value, str) and value for value in required.values()):
        return False

    username = required[_CONCIERGE_ROOT_ADMIN_USERNAME_ENV]
    password_hash = required[_CONCIERGE_ROOT_ADMIN_PASSWORD_HASH_ENV]
    session_secret = required[_CONCIERGE_ROOT_SESSION_SECRET_ENV]
    proxy_secret = required[_CONCIERGE_ROOT_PROXY_SECRET_ENV]
    backend_api_key = required[_CONCIERGE_ROOT_BACKEND_API_KEY_ENV]
    vworld_key = required[_CONCIERGE_ROOT_VWORLD_KEY_ENV]
    if (
        username != username.strip()
        or any(character.isspace() for character in username)
        or any("\r" in value or "\n" in value for value in required.values())
        or not is_pbkdf2_sha256_password_hash(password_hash)
        or not backend_api_key.strip()
        or not vworld_key.strip()
    ):
        return False
    api_keys = values.get(_CONCIERGE_ROOT_API_KEYS_ENV, "")
    if not isinstance(api_keys, str):
        return False
    configured_api_keys = api_keys.split(",")
    if (
        not api_keys
        or any(not key or key != key.strip() for key in configured_api_keys)
        or backend_api_key not in configured_api_keys
    ):
        return False
    if (
        values.get(_CONCIERGE_ROOT_APP_ENV) != "production"
        or values.get(_CONCIERGE_ROOT_API_AUTH_ENABLED_ENV) != "true"
    ):
        return False
    if len(session_secret) < 32 or any(
        character.isspace() for character in session_secret
    ):
        return False
    if len(proxy_secret) < 32 or any(
        character.isspace() for character in proxy_secret
    ):
        return False

    trust_forwarded_ips = values.get(_CONCIERGE_ROOT_TRUST_FORWARDED_IPS_ENV, "false")
    if trust_forwarded_ips not in {"true", "false"}:
        return False
    if values.get(_CONCIERGE_ROOT_PUBLIC_API_BASE_ENV, ""):
        return False

    raw_origins = values.get(_CONCIERGE_ROOT_PUBLIC_ORIGINS_ENV, "")
    if not isinstance(raw_origins, str):
        return False
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if raw_origins and (not origins or ",".join(origins) != raw_origins):
        return False
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
    return len(origins) == len(set(origins))


#: "이 후보가 concierge를 실제로 세우는가"의 신호. stub은 `image` 하나뿐이고,
#: 배포되는 서비스는 이 키들 중 하나 이상에 **값**을 갖는다.
#:
#: **키 존재가 아니라 값이 있는지를 본다.** `docker compose config`가 stub에도
#: `command: null`·`entrypoint: null`을 붙이기 때문이다(실측: raw stub은 `['image']`
#: 인데 resolved stub은 `['command', 'entrypoint', 'image', 'networks']`). 키로
#: 판정하면 resolved에서 모든 stub이 "구성됨"으로 오인된다.
#:
#: **집합이 좁으면 우회로가 된다.** 첫 판은 `environment`·`command`·`network_mode`
#: 셋뿐이었고, 적대 리뷰 2026-09-18이 `entrypoint`로 `--host 0.0.0.0`을 주고
#: `ports`로 전 인터페이스에 게시하고 `env_file`로 concierge `.env`(= proxy secret이
#: 있는 파일)를 읽는 후보가 **세 신호를 한 글자도 건드리지 않고** 통과하는 것을
#: 실측했다. 새 키를 더할 때는 "그 키에 값이 있으면 이 서비스는 실제로 배포된다"가
#: 참인지만 물어라 — 정당한 stub이 그 키를 값으로 갖지 않으면 비용은 0이다.
#: compose가 stub에도 `null`로 붙이는 키, **그리고 `environment`** — 존재가 아니라
#: 값이 `None`이 아님으로 본다.
#:
#: `environment`가 여기 있는 것이 중요하다. 라운드 3에서 그것을 truthy 쪽으로 옮겼고,
#: 그 결과 `{image, environment: {}}`인 concierge-api가 UI 없이 계약을 **통째로**
#: 빠져나갔다 — 2,836형상 대조에서 **약화 24칸**이 전부 그 결함군이었다(적대 리뷰
#: 2026-09-18 라운드4 F1). compose는 stub에 `environment`를 붙이지 않으므로 빈 dict·
#: 빈 list는 "이 서비스를 실제로 선언했다"는 신호다.
_CONCIERGE_NULLABLE_DEPLOYMENT_SIGNALS: Final = (
    "command",
    "entrypoint",
    "environment",
    "network_mode",
)
#: 붙지 않는 키 — 빈 리스트·빈 dict는 "배포한다"는 신호가 아니므로 **truthy**로 본다.
_CONCIERGE_PRESENT_DEPLOYMENT_SIGNALS: Final = ("ports", "env_file", "build")
_CONCIERGE_API_DEPLOYMENT_SIGNALS = (
    *_CONCIERGE_NULLABLE_DEPLOYMENT_SIGNALS,
    *_CONCIERGE_PRESENT_DEPLOYMENT_SIGNALS,
)


def _validate_concierge_ui_canonical_contract(
    services: Mapping[str, Any],
    environment: Mapping[str, str],
    *,
    resolved: bool,
) -> None:
    """Concierge UI/API의 raw/resolved single-file boundary를 같은 계약으로 검사한다.

    Concierge 전체 `.env`는 provider와 server-only 키를 함께 담으므로 UI에 주입할
    수 없다. UI와 API를 함께 검사해, BFF proxy authority가 source `.env` 값으로
    갈라지는 drift를 차단한다.

    **게이트가 "UI 존재"에서 "UI 존재 또는 API가 구성됨"으로 바뀌었다**(2026-09-18).
    종전에는 `UI not in services`면 함수째 return했고, 그 안에 **API의 계약 전부와
    Manager root env 불변식**이 들어 있었다. concierge 두 서비스는 required set 밖이라
    후보에서 UI 한 줄만 지우면 API는 남은 채 그 전부가 조용히 꺼졌다 — 실측으로
    `API_AUTH_ENABLED=false` + `network_mode: bridge` + 임의 proxy secret인 API가 두
    진입점을 통과했다. 방향도 비대칭이었다(API 삭제는 거부, **UI 삭제는 통과**).
    raw 정본 기본값이 `${KOR_TRAVEL_CONCIERGE_API_AUTH_ENABLED:-false}`라 **compose를
    한 글자도 안 바꿔도** UI 한 줄 삭제 + root env 미설정만으로 통과했고, API는
    `--host 0.0.0.0` + `network_mode: host`이므로 대상은 전 인터페이스였다.

    **"API가 있으면 검사"는 틀린 처방이다.** `_compose_fragment`가 의존성으로만
    끌어온 서비스는 `image` 하나뿐인 **stub**이고(`concierge-api`·`geo-api`·`rustfs`가
    같은 모양), Map API가 concierge를 HTTP로 부르므로 **Map 단독 target 후보에는
    concierge-api가 stub으로 들어온다.** stub에 전체 계약을 요구하면 정당한 배포가
    거부된다(실측: 9건 빨감). UI 게이트에는 이유가 있었다 — UI의 존재가 "concierge를
    실제로 배포한다"는 신호다. 그래서 **stub이냐 구성됨이냐**로 가른다.

    **검사 순서는 한 줄도 바꾸지 않았다.** 각 검사에 자기 주체의 조건만 달았다.
    """

    # **키의 존재로 본다.** `services.get(...)`으로 읽으면 `ui: null`이 부재와
    # 구분되지 않는다 — S1이 `_shape_nulled`로 명시적으로 박은 규칙("null을 부재로
    # 오인하면 계약을 한 줄로 우회할 수 있다")의 위반이다. 오늘은 진입점의
    # non-Mapping 스캔이 더 앞에서 막아 주지만, 이 함수가 단독으로도 안전해야 한다.
    ui_declared = _CONCIERGE_UI_SERVICE in services
    ui_service = services.get(_CONCIERGE_UI_SERVICE)
    api_service = services.get(_CONCIERGE_API_SERVICE)
    # `command`/`entrypoint`는 compose가 stub에도 `null`을 붙이므로 **`is not None`**이
    # 옳다. 나머지는 그 이유가 없고 빈 리스트·빈 dict가 흔한 관용구라 **truthy**로
    # 본다(적대 리뷰 2026-09-18 C-F5).
    api_is_configured = isinstance(api_service, Mapping) and (
        any(
            api_service.get(key) is not None
            for key in _CONCIERGE_NULLABLE_DEPLOYMENT_SIGNALS
        )
        or any(
            api_service.get(key) for key in _CONCIERGE_PRESENT_DEPLOYMENT_SIGNALS
        )
    )
    if not ui_declared and not api_is_configured:
        # UI가 없고 API도 stub이다 — 지킬 대상이 없다(Map 단독 target의 정상 형상).
        return
    if ui_declared:
        # UI가 있으면 API도 있어야 한다(오늘 그대로) — UI는 자기 backend 없이 설 수 없다.
        if not isinstance(ui_service, Mapping) or not isinstance(api_service, Mapping):
            raise ComposeCandidateContractError(
                "Concierge UI canonical contract requires valid API and UI services"
            )
    if ui_service is not None and "env_file" in ui_service:
        raise ComposeCandidateContractError(
            "Concierge UI must not load an env_file at the single-file boundary"
        )
    ui_environment = ui_service.get("environment") if ui_service is not None else None
    api_environment = api_service.get("environment")
    if not isinstance(api_environment, Mapping) or (
        ui_service is not None and not isinstance(ui_environment, Mapping)
    ):
        raise ComposeCandidateContractError(
            "Concierge API and UI must use mapping environment"
        )
    if ui_environment is not None and set(ui_environment) != set(
        _CONCIERGE_UI_CANONICAL_RAW_ENV_VALUES
    ):
        raise ComposeCandidateContractError(
            "Concierge UI environment must be the exact canonical allowlist"
        )
    if not _concierge_ui_root_values_are_valid(environment):
        raise ComposeCandidateContractError(
            "Concierge UI Manager root environment is invalid"
        )
    if environment.get("KOR_TRAVEL_CONCIERGE_API_PORT", "12601") != "12601":
        raise ComposeCandidateContractError(
            "Concierge UI backend origin must keep the canonical loopback API port"
        )
    if environment.get("KOR_TRAVEL_CONCIERGE_UI_PORT", "12605") != "12605":
        raise ComposeCandidateContractError(
            "Concierge UI must keep the canonical production port"
        )
    expected_network_mode = (
        _CONCIERGE_CANONICAL_RESOLVED_NETWORK_MODE
        if resolved
        else _CONCIERGE_CANONICAL_RAW_NETWORK_MODE
    )
    for service_name, service in (
        (_CONCIERGE_API_SERVICE, api_service),
        (_CONCIERGE_UI_SERVICE, ui_service),
    ):
        if service is None:
            continue
        network_mode = service.get("network_mode")
        if not isinstance(network_mode, str) or not hmac.compare_digest(
            network_mode, expected_network_mode
        ):
            raise ComposeCandidateContractError(
                f"Concierge {service_name} must keep the canonical host network boundary"
            )

    expected_api_command = (
        _CONCIERGE_API_CANONICAL_RESOLVED_COMMAND
        if resolved
        else _CONCIERGE_API_CANONICAL_RAW_COMMAND
    )
    api_command = api_service.get("command")
    if not isinstance(api_command, list) or tuple(api_command) != expected_api_command:
        raise ComposeCandidateContractError(
            "Concierge API must keep the canonical loopback BFF command"
        )
    if ui_service is not None:
        expected_ui_command = _concierge_ui_expected_command(
            environment, resolved=resolved
        )
        ui_command = ui_service.get("command")
        if not isinstance(ui_command, list) or tuple(ui_command) != expected_ui_command:
            raise ComposeCandidateContractError(
                "Concierge UI must keep the canonical production command"
            )

    if resolved:
        expected_ui_environment = {
            target: (
                _CONCIERGE_FIXED_BACKEND_ORIGIN
                if target == _CONCIERGE_UI_BACKEND_ORIGIN_ENV
                else _compose_resolved_escaped_value(
                    environment.get(source_name, "false" if target == _CONCIERGE_UI_TRUST_FORWARDED_IPS_ENV else "")
                )
            )
            for target, source_name in _CONCIERGE_UI_ENV_SOURCES.items()
        }
        expected_ui_environment[_CONCIERGE_UI_BACKEND_ORIGIN_ENV] = (
            _CONCIERGE_FIXED_BACKEND_ORIGIN
        )
    else:
        expected_ui_environment = _CONCIERGE_UI_CANONICAL_RAW_ENV_VALUES
    for target_name, expected in (
        expected_ui_environment.items() if ui_environment is not None else ()
    ):
        actual = ui_environment.get(target_name)
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            raise ComposeCandidateContractError(
                f"Concierge UI {target_name} canonical wiring is invalid"
            )

    for target_name, canonical in _CONCIERGE_API_CANONICAL_RAW_ENV_VALUES.items():
        expected = (
            _compose_resolved_escaped_value(
                environment[
                    {
                        _CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV: _CONCIERGE_ROOT_PROXY_SECRET_ENV,
                        "APP_ENV": _CONCIERGE_ROOT_APP_ENV,
                        "API_AUTH_ENABLED": _CONCIERGE_ROOT_API_AUTH_ENABLED_ENV,
                        "API_KEYS": _CONCIERGE_ROOT_API_KEYS_ENV,
                    }[target_name]
                ]
            )
            if resolved
            else canonical
        )
        actual = api_environment.get(target_name)
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            if target_name == _CONCIERGE_UI_ADMIN_PROXY_SECRET_ENV:
                raise ComposeCandidateContractError(
                    "Concierge API and UI must share the canonical Manager proxy authority"
                )
            raise ComposeCandidateContractError(
                f"Concierge API {target_name} canonical wiring is invalid"
            )


def _concierge_ui_expected_command(
    environment: Mapping[str, str], *, resolved: bool
) -> tuple[str, ...]:
    if not resolved:
        return _CONCIERGE_UI_CANONICAL_RAW_COMMAND
    return (
        *_CONCIERGE_UI_CANONICAL_RAW_COMMAND[:2],
        _CONCIERGE_UI_CANONICAL_RAW_COMMAND[2].replace(
            "${KOR_TRAVEL_CONCIERGE_UI_PORT:-12605}", "12605"
        ),
    )


def validate_concierge_ui_canonical_compose_boundary(
    candidate: Mapping[str, Any],
    resolved: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
) -> None:
    """legacy override 퇴역 전 Concierge UI/API C6c raw·resolved 경계를 함께 검증한다."""

    candidate_services = candidate.get("services")
    resolved_services = resolved.get("services")
    if not isinstance(candidate_services, Mapping) or not isinstance(
        resolved_services, Mapping
    ):
        raise ComposeCandidateContractError(
            "Concierge canonical Compose boundary has no valid services mapping"
        )
    for services, resolved_document in (
        (candidate_services, False),
        (resolved_services, True),
    ):
        if {
            _CONCIERGE_API_SERVICE,
            _CONCIERGE_UI_SERVICE,
        }.difference(services):
            raise ComposeCandidateContractError(
                "Concierge canonical Compose boundary is missing API or UI service"
            )
        _validate_concierge_ui_canonical_contract(
            services,
            environment,
            resolved=resolved_document,
        )


def _compose_resolved_escaped_value(value: str) -> str:
    """Compose resolved JSON이 literal `$`를 표현하는 결정적 값을 반환한다."""

    return value.replace("$", "$$")


def _parse_port(value: str, env_name: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise DeploymentContractError(f"{env_name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise DeploymentContractError(f"{env_name} must be between 1 and 65535")
    return port


def validate_c6c_operation_tokens(
    environment: Mapping[str, str],
    *,
    require_nonempty: bool,
) -> None:
    """Map operation capability 세트의 완결성·형식을 검증한다.

    F1D는 final fixture smoke까지 같은 capability 세트를 소비하므로 rehearsal에서도
    후보 image build나 DB reset보다 먼저 non-empty 세 token을 요구한다.
    """

    _validate_raw_token_pair(
        environment.get(_MAP_READ_ENV, ""),
        environment.get(_MAP_CANCEL_ENV, ""),
        environment.get(_MAP_FIXTURE_ENV, ""),
        require_nonempty=require_nonempty,
    )


def _validate_raw_token_pair(
    read_token: str,
    cancel_token: str,
    fixture_token: str,
    *,
    require_nonempty: bool,
) -> None:
    tokens = (
        (_MAP_READ_ENV, read_token),
        (_MAP_CANCEL_ENV, cancel_token),
        (_MAP_FIXTURE_ENV, fixture_token),
    )
    if not any(token for _, token in tokens):
        if require_nonempty:
            raise DeploymentContractError("production C6c tokens must all be configured")
        return
    if any(not token for _, token in tokens):
        raise DeploymentContractError("C6c read, cancel, and fixture tokens must be configured together")
    for env_name, token in tokens:
        if len(token) < 32:
            raise DeploymentContractError(f"{env_name} must contain at least 32 characters")
        if any(character.isspace() for character in token):
            raise DeploymentContractError(f"{env_name} must not contain whitespace")
    if len({read_token, cancel_token, fixture_token}) != 3:
        raise DeploymentContractError("C6c read, cancel, and fixture tokens must differ")


def derive_curation_service_principal_environment(
    environment: Mapping[str, str],
    *,
    error_type: type[DeploymentContractError] = DeploymentContractError,
) -> dict[str, str]:
    """PinVi 원시 principal pair에서 Map 검증용 digest만 파생한다.

    Map runtime에는 원시 ServiceToken을 전달하지 않는다. 기존 C6c deployment가
    T-VN-40 service cutover 전에도 계속 동작하도록 pair가 모두 비어 있는 경우만
    허용하며, digest만 외부에서 주입하는 우회는 fail-close한다.
    """

    values = dict(environment)
    snapshot_token = values.get(_PINVI_CURATION_SNAPSHOT_ENV, "")
    mapping_token = values.get(_PINVI_CUTOVER_MAPPING_ENV, "")
    declared_snapshot_digest = values.get(_MAP_CURATION_SNAPSHOT_DIGEST_ENV, "")
    declared_mapping_digest = values.get(
        _MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV,
        "",
    )
    raw_tokens = (
        (_PINVI_CURATION_SNAPSHOT_ENV, snapshot_token),
        (_PINVI_CUTOVER_MAPPING_ENV, mapping_token),
    )
    declared_digests = (
        (_MAP_CURATION_SNAPSHOT_DIGEST_ENV, declared_snapshot_digest),
        (_MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV, declared_mapping_digest),
    )

    if any(not isinstance(value, str) for _, value in (*raw_tokens, *declared_digests)):
        raise error_type("T-VN-40 curation service principal environment is invalid")
    if not any(value for _, value in raw_tokens):
        if any(value for _, value in declared_digests):
            raise error_type(
                "Map curation token digests require the corresponding PinVi raw pair"
            )
        values[_MAP_CURATION_SNAPSHOT_DIGEST_ENV] = ""
        values[_MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV] = ""
        return values
    if any(not value for _, value in raw_tokens):
        raise error_type(
            "PinVi curation snapshot and cutover-mapping tokens must be configured together"
        )
    for env_name, token in raw_tokens:
        if len(token) < 32:
            raise error_type(f"{env_name} must contain at least 32 characters")
        if any(character.isspace() for character in token):
            raise error_type(f"{env_name} must not contain whitespace")
    if hmac.compare_digest(snapshot_token, mapping_token):
        raise error_type("PinVi curation snapshot and cutover-mapping tokens must differ")

    expected_snapshot_digest = hashlib.sha256(snapshot_token.encode("utf-8")).hexdigest()
    expected_mapping_digest = hashlib.sha256(mapping_token.encode("utf-8")).hexdigest()
    for env_name, actual, expected in (
        (
            _MAP_CURATION_SNAPSHOT_DIGEST_ENV,
            declared_snapshot_digest,
            expected_snapshot_digest,
        ),
        (
            _MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV,
            declared_mapping_digest,
            expected_mapping_digest,
        ),
    ):
        if actual and not hmac.compare_digest(actual, expected):
            raise error_type(f"{env_name} must be derived from its PinVi raw token")
    values[_MAP_CURATION_SNAPSHOT_DIGEST_ENV] = expected_snapshot_digest
    values[_MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV] = expected_mapping_digest
    return values


def _validate_feature_create_credentials(
    environment: Mapping[str, str],
    *,
    error_type: type[DeploymentContractError] = DeploymentContractError,
    require_nonempty: bool,
) -> None:
    """Map manual Feature 생성 원문/digest의 단일 정본을 검증한다.

    원문은 UI server runtime에서만 소비되고 API에는 SHA-256 digest만 들어간다.
    두 입력을 따로 provision하면 서로 다른 credential이 배선될 수 있으므로,
    Manager가 Compose interpolation 전에 원문에서 digest를 재계산해 비교한다.
    """

    raw = environment.get(_MAP_FEATURE_CREATE_TOKEN_ENV, "")
    digest = environment.get(_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV, "")
    if not isinstance(raw, str) or not isinstance(digest, str):
        raise error_type("Map manual Feature create credential environment is invalid")
    if not raw and not digest:
        if require_nonempty:
            raise error_type(
                f"{_MAP_FEATURE_CREATE_TOKEN_ENV} and "
                f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must be configured"
            )
        return
    if not raw or not digest:
        raise error_type(
            f"{_MAP_FEATURE_CREATE_TOKEN_ENV} and "
            f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must be configured together"
        )
    if len(raw) < 32 or any(character.isspace() for character in raw):
        raise error_type(
            f"{_MAP_FEATURE_CREATE_TOKEN_ENV} must contain at least 32 characters "
            "without whitespace"
        )
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise error_type(
            f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must be lowercase SHA-256 hex"
        )
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise error_type(
            f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must be derived from "
            f"{_MAP_FEATURE_CREATE_TOKEN_ENV}"
        )


def _cache_target_token_digests(values: Mapping[str, str]) -> tuple[str, ...]:
    raw = values.get(_MAP_CACHE_TARGET_PRINCIPALS_ENV, "")
    if not raw:
        return ()
    try:
        principals = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(principals, list):
        return ()
    digests: list[str] = []
    for principal in principals:
        if not isinstance(principal, Mapping):
            continue
        digest = principal.get("token_sha256")
        if isinstance(digest, str):
            digests.append(digest)
    return tuple(digests)


def _validate_feature_create_credential_distinctness(
    values: Mapping[str, str],
    *,
    error_type: type[DeploymentContractError],
) -> None:
    raw = values.get(_MAP_FEATURE_CREATE_TOKEN_ENV, "")
    digest = values.get(_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV, "")
    if not raw or not digest:
        return

    def same_secret(left: str, right: str) -> bool:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))

    raw_credential_names = (
        _MAP_ADMIN_PROXY_ENV,
        _MAP_SERVICE_TOKEN_ENV,
        _MAP_READ_ENV,
        _MAP_CANCEL_ENV,
        _MAP_FIXTURE_ENV,
        _MAP_CURSOR_SIGNING_SECRET_ENV,
        _MAP_METRICS_TOKEN_ENV,
        _MAP_GEO_API_KEY_SOURCE_ENV,
        _MAP_UI_PASSWORD_HASH_ENV,
        _MAP_UI_SESSION_SECRET_ENV,
        _MAP_UI_PASSWORD_ENV,
        *_CURATION_PRINCIPAL_RAW_ENV_NAMES,
    )
    for env_name in raw_credential_names:
        protected = values.get(env_name, "")
        if protected and same_secret(raw, protected):
            raise error_type(
                f"{_MAP_FEATURE_CREATE_TOKEN_ENV} must differ from {env_name}"
            )
        if protected and hmac.compare_digest(
            digest,
            hashlib.sha256(protected.encode("utf-8")).hexdigest(),
        ):
            raise error_type(
                f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must differ from {env_name}"
            )

    for env_name, protected_digest in (
        (_MAP_CURATION_SNAPSHOT_DIGEST_ENV, values.get(_MAP_CURATION_SNAPSHOT_DIGEST_ENV, "")),
        (
            _MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV,
            values.get(_MAP_CURATION_CUTOVER_MAPPING_DIGEST_ENV, ""),
        ),
    ):
        if protected_digest and same_secret(digest, protected_digest):
            raise error_type(
                f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must differ from {env_name}"
            )
    if any(
        same_secret(digest, principal_digest)
        for principal_digest in _cache_target_token_digests(values)
    ):
        raise error_type(
            f"{_MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV} must differ from cache-target tokens"
        )


def _validate_map_production_secrets(config: C6cDeploymentConfig) -> None:
    _validate_map_production_secret_values(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": config.deployment_environment,
            _MAP_ADMIN_PROXY_ENV: config.map_admin_proxy_secret,
            _MAP_SERVICE_TOKEN_ENV: config.map_service_token,
            _MAP_CURSOR_SIGNING_SECRET_ENV: config.map_cursor_signing_secret,
            _MAP_GEO_API_KEY_SOURCE_ENV: config.map_geo_api_key,
            _MAP_READ_ENV: config.read_token,
            _MAP_CANCEL_ENV: config.cancel_token,
            _MAP_FIXTURE_ENV: config.fixture_token,
            _PINVI_CURATION_SNAPSHOT_ENV: config.curation_snapshot_token,
            _PINVI_CUTOVER_MAPPING_ENV: config.curation_cutover_mapping_token,
            _MAP_FEATURE_CREATE_TOKEN_ENV: config.feature_create_token,
            _MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV: config.feature_create_token_digest,
            _MAP_FEATURE_CREATE_ENABLED_ENV: config.feature_create_enabled,
            _MAP_UI_PASSWORD_HASH_ENV: config.map_ui_password_hash,
            _MAP_UI_SESSION_SECRET_ENV: config.map_ui_session_secret,
            _MAP_UI_PASSWORD_ENV: config.smoke.map_ui_password,
            "KTDM_C6C_PINVI_ADMIN_EMAIL": config.smoke.pinvi_admin_email,
            _PINVI_ADMIN_PASSWORD_ENV: config.smoke.pinvi_admin_password,
            "KTDM_C6C_CONTRACT_GENERATION": config.contract_generation,
        },
        reject_published_examples=config.production,
    )


def _validate_map_production_secret_values(
    values: Mapping[str, str],
    *,
    error_type: type[DeploymentContractError] = DeploymentContractError,
    reject_published_examples: bool = False,
) -> None:
    derive_curation_service_principal_environment(values, error_type=error_type)
    _validate_feature_create_credentials(
        values,
        error_type=error_type,
        require_nonempty=values.get("KTDM_DEPLOYMENT_ENVIRONMENT")
        in {"rehearsal", "production"},
    )
    feature_create_enabled = values.get(_MAP_FEATURE_CREATE_ENABLED_ENV, "false")
    if feature_create_enabled not in {"true", "false"}:
        raise error_type(
            f"{_MAP_FEATURE_CREATE_ENABLED_ENV} must be exactly true or false"
        )
    _validate_feature_create_credential_distinctness(
        values,
        error_type=error_type,
    )
    geo_api_key = values.get(_MAP_GEO_API_KEY_SOURCE_ENV, "")
    geo_api_key_required = values.get("KTDM_DEPLOYMENT_ENVIRONMENT") in {
        "production",
        "rehearsal",
    }
    if geo_api_key_required and not geo_api_key:
        raise error_type(f"{_MAP_GEO_API_KEY_SOURCE_ENV} must be explicitly set")
    if geo_api_key_required and (
        len(geo_api_key) != 32
        or not geo_api_key.isascii()
        or not geo_api_key.isalnum()
    ):
        raise error_type(f"{_MAP_GEO_API_KEY_SOURCE_ENV} is invalid")
    if not geo_api_key_required and geo_api_key and (
        geo_api_key != geo_api_key.strip()
        or len(geo_api_key) > 128
        or any(character.isspace() for character in geo_api_key)
    ):
        raise error_type(f"{_MAP_GEO_API_KEY_SOURCE_ENV} is invalid")
    if values.get("KTDM_DEPLOYMENT_ENVIRONMENT") == "production":
        try:
            _validate_raw_token_pair(
                values.get(_MAP_READ_ENV, ""),
                values.get(_MAP_CANCEL_ENV, ""),
                values.get(_MAP_FIXTURE_ENV, ""),
                require_nonempty=True,
            )
        except DeploymentContractError as exc:
            raise error_type(str(exc)) from exc
    new_secrets = tuple(
        (env_name, values.get(env_name, ""))
        for env_name in (
            _MAP_ADMIN_PROXY_ENV,
            _MAP_SERVICE_TOKEN_ENV,
            _MAP_CURSOR_SIGNING_SECRET_ENV,
        )
    )
    for env_name, secret in new_secrets:
        if not isinstance(secret, str) or len(secret) < 32:
            raise error_type(
                f"{env_name} must contain at least 32 characters"
            )
        if any(character.isspace() for character in secret):
            raise error_type(f"{env_name} must not contain whitespace")
        if reject_published_examples and hmac.compare_digest(
            secret,
            _MAP_PUBLISHED_EXAMPLE_SECRET_VALUES[env_name],
        ):
            raise error_type(
                f"{env_name} must not use the published local example value "
                "in production"
            )

    protected_credential_names = (
        _MAP_READ_ENV,
        _MAP_CANCEL_ENV,
        _MAP_FIXTURE_ENV,
        _PINVI_CURATION_SNAPSHOT_ENV,
        _PINVI_CUTOVER_MAPPING_ENV,
        _MAP_UI_PASSWORD_HASH_ENV,
        _MAP_UI_SESSION_SECRET_ENV,
        _MAP_UI_PASSWORD_ENV,
        "KTDM_C6C_PINVI_ADMIN_EMAIL",
        _PINVI_ADMIN_PASSWORD_ENV,
        "KTDM_C6C_CONTRACT_GENERATION",
        _MAP_GEO_API_KEY_SOURCE_ENV,
    )
    compared: list[tuple[str, str]] = []
    for env_name, secret in (
        *new_secrets,
        *((name, values.get(name, "")) for name in protected_credential_names),
    ):
        if not isinstance(secret, str) or not secret:
            continue
        for previous_name, previous_secret in compared:
            if hmac.compare_digest(secret, previous_secret):
                raise error_type(
                    f"{env_name} must differ from {previous_name}"
                )
        compared.append((env_name, secret))


def _validate_production_config(
    config: C6cDeploymentConfig,
    values: Mapping[str, str],
) -> None:
    if config.map_container != "kor-travel-map-api-latest":
        raise DeploymentContractError(
            "production C6c deployment requires the canonical Map API container identity"
        )
    if config.pinvi_container != "pinvi-api-latest":
        raise DeploymentContractError(
            "production C6c deployment requires the canonical PinVi API container identity"
        )
    if config.map_ui_container != "kor-travel-map-ui-latest":
        raise DeploymentContractError(
            "production C6c deployment requires the canonical Map UI container identity"
        )
    if config.map_container_port != 12701:
        raise DeploymentContractError(
            "production KOR_TRAVEL_MAP_API_CONTAINER_PORT must be exactly 12701"
        )
    if values.get("KTDM_DOCKER_NETWORK_MODE", "").strip().lower() != "host":
        raise DeploymentContractError(
            "production C6c deployment requires KTDM_DOCKER_NETWORK_MODE=host"
        )
    if not values.get("PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL", "").strip():
        raise DeploymentContractError(
            "production C6c deployment requires an explicit PinVi Map base URL"
        )
    try:
        parsed = urlsplit(config.base_url)
        port = parsed.port
    except ValueError as exc:
        raise DeploymentContractError(
            "PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL must be a valid host-network URL"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port != 12701
    ):
        raise DeploymentContractError(
            "production PINVI_KOR_TRAVEL_MAP_ADMIN_BASE_URL must be "
            "exactly http://127.0.0.1:12701"
        )

    smoke = config.smoke
    identities = (
        (_MAP_UI_USERNAME_ENV, smoke.map_ui_username),
        ("KTDM_C6C_PINVI_ADMIN_EMAIL", smoke.pinvi_admin_email),
    )
    passwords = (
        (_MAP_UI_PASSWORD_ENV, smoke.map_ui_password),
        (_PINVI_ADMIN_PASSWORD_ENV, smoke.pinvi_admin_password),
    )
    for env_name, value in identities:
        if not value:
            raise DeploymentContractError(f"{env_name} must be configured for production smoke")
        if "\r" in value or "\n" in value:
            raise DeploymentContractError(f"{env_name} must not contain line breaks")
    for env_name, value in passwords:
        if len(value) < 16:
            raise DeploymentContractError(
                f"{env_name} must contain at least 16 characters for production smoke"
            )
        if "\r" in value or "\n" in value:
            raise DeploymentContractError(f"{env_name} must not contain line breaks")


def validate_resolved_compose_candidate_protected_values(
    resolved: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
    compose_path: str | None = None,
    root_env_path: str | None = None,
) -> tuple[CandidateSystemBindSnapshot, ...]:
    """resolved compose 전체 graph의 C6c 보호 이름·현재 값을 검사한다."""

    environment = derive_curation_service_principal_environment(
        environment,
        error_type=ComposeCandidateContractError,
    )
    _validate_map_production_secret_values(
        environment,
        error_type=ComposeCandidateContractError,
        reject_published_examples=(environment.get("KTDM_DEPLOYMENT_ENVIRONMENT") == "production"),
    )
    _assert_candidate_single_file_boundary(resolved, environment=environment)
    map_dsn_identity = _validate_map_database_dsn_identities(environment)
    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "resolved compose candidate has no valid services mapping"
        )
    # **required-set을 여섯 validator보다 먼저 본다**(GM-17 B S1).
    # 종전에는 순서가 반대였고, 그래서 서비스가 빠지면 사용자가 보는 것은 부재가
    # 아니라 "Map PostgreSQL password secret is invalid" 같은 **무관해 보이는**
    # 문구였다(S0 골든 테이블 실측: 여섯 형상 중 부재를 부재라고 말하는 것은 하나뿐).
    # 운영자는 그 메시지를 쫓다가 실제 원인에 도달하지 못한다. 집합 자체는 바꾸지
    # 않는다 — 완화는 S4의 일이고 여기서는 진단만 고친다.
    missing_services = _CANDIDATE_REQUIRED_PROTECTED_SERVICES.difference(services)
    if missing_services:
        raise ComposeCandidateContractError(
            "resolved compose candidate is missing required protected services: "
            + ", ".join(sorted(missing_services))
        )
    # **서비스 값의 모양을 먼저 본다**(GM-17 B S1). `pinvi-api: null`은 유효한 YAML이라
    # required-set을 지나가고(키는 있다), 그 뒤 소비자 스캔이 non-Mapping을 만나
    # **어느 서비스를 null로 만들든 "Map PostgreSQL password secret is invalid"**를
    # 낸다(S0 골든 테이블 실측). 원인과 무관한 메시지다.
    #
    # 그리고 이 검사는 S4의 안전 조건이기도 하다 — 완화의 skip 판정은 **키 부재로만**
    # 해야 하는데, null을 부재로 오인하면 계약을 한 줄로 우회할 수 있다. 여기서
    # null을 "부재"가 아니라 "invalid"로 못박아 그 혼동의 여지를 없앤다.
    for service_name, service_document in services.items():
        if isinstance(service_document, Mapping):
            continue
        raise ComposeCandidateContractError(
            "resolved compose candidate service is missing or invalid: "
            + _describe_candidate_service_key(service_name)
        )
    # ── instance admin secret : **family scope 밖의 전역 불변식** ──────────
    # 아래 전역 블록의 `_assert_instance_admin_secret_holders`는 family validator와 **다른
    # 층**이다. S4가 family scope로 게이팅하더라도 그대로 돈다 — 그것이 요점이다.
    #
    # 적대 리뷰 2026-09-17이 첫 판을 뚫었다: 전역 불변식이 Map validator **안에**
    # 있어서, 호출부를 Map PostgreSQL의 존재로 감싸는 순진한 S4가 전체 스위트 1700건을
    # 그대로 통과했고, 그 상태에서 `pinvi-api`가 Map superuser password를 마운트하는
    # 후보가 mutation 경계를 **통과**했다. ADR-53부터 그 규칙은 이름이 아니라 모든
    # PostgreSQL 서버의 admin secret에서 유도하고, 서버 형태 술어 **뒤에** 선다 — 무엇이
    # 서버인지 확정한 뒤에 그 secret을 누가 드는지 본다.
    #
    # **여기에 family 조건을 달지 마라.** 소유자가 없다고 남의 소비가 인가되지 않는다.

    # ── family scope 밖의 전역 불변식 ─────────────────────────────────────
    # **자리는 main과 같고(메시지 보존), 조건은 걸리지 않는다(S4 방어).**
    # 그 둘이 다른 축이라는 것이 적대 리뷰 2026-09-17의 정정이다 — 처음에는 "밖"을
    # 물리적 위치로 읽고 블록 앞뒤로 흩어 놓았는데, 그 탓에 1,434 형상 중 215칸의
    # 메시지가 바뀌었다. main에서 소비자 스캔은 (A)뿐 아니라 **같은 family의
    # validator 전부보다 앞**이었다.
    #
    # **이 줄들에 family 조건을 달지 마라.** 아래 family validator들이 scope로
    # 게이팅되어도 이 줄들은 그대로 돈다 — 그것이 S2·S3의 전부다.
    _assert_no_postgres_auth_override(resolved)
    # 위와 **대칭인** 전역 술어다 — 그쪽은 키의 존재를 막고 이쪽은 값을 묶는다.
    # 자리는 바로 뒤다(메시지 보존을 900형상으로 실측했다).
    _assert_canonical_postgres_initdb_args(resolved)
    # GM-17 B S3-c — 종전 한 줄을 둘로 편다. **자리는 그대로다**(감사 실측:
    # 제자리 분할은 396형상에서 메시지 변경 0칸, Map DSN 자리로 올리면 46칸이
    # 바뀌고 그중 일부는 S1의 "부재를 부재라고 말하기"를 되돌린다).
    #
    # 첫 줄은 **전역 env 불변식**이라 어떤 family 조건도 달지 않는다. 둘째 줄이
    # S4가 게이팅할 수 있는 per-service 절반이다.
    _pinvi_database_identity = _validate_pinvi_database_url_environment(environment)
    _validate_pinvi_database_url_service_identities(
        services, _pinvi_database_identity, resolved=True
    )
    # PostgreSQL 서버의 실행 형태(loopback 결박 포함)는 아래 전역 술어 하나가
    # 모든 선언·목격된 PostgreSQL에 대해 본다 — 서비스 이름을 열거하지 않는다.
    # 특권 축은 PostgreSQL보다 넓다 — 어떤 서비스도 호스트 권한을 가져갈 수
    # 없다.
    _assert_no_host_privilege_escalation(resolved)
    _assert_postgres_cluster_runtime_is_canonical(resolved)
    # 서버가 확정된 뒤다: Map DSN 포트의 instance와, 모든 서버의 admin secret을 누가 드는지.
    _validate_map_database_dsn_instance(
        environment, resolved=resolved, identity=map_dsn_identity
    )
    _assert_instance_admin_secret_holders(resolved, environment=environment, resolved=True)
    _validate_concierge_ui_canonical_contract(services, environment, resolved=True)
    _validate_map_application_300_images(services)

    for service_name in _candidate_protected_service_order():
        # **무조건 인덱싱하지 않는다**(GM-17 B S1). 종전 `services[service_name]`은
        # 이름이 빠지면 raw `KeyError`를 던졌고, 그것이 계약 오류가 아니라 traceback으로
        # 사용자에게 샜다.
        #
        # 이 루프의 이름은 전부 `_CANDIDATE_REQUIRED_PROTECTED_SERVICES` 안에 있다.
        # (2026-09-28까지는 `pinvi-db-init` 하나가 그 밖이었고 보증의 출처가 따로
        # 있었다 — 그 one-shot은 옛 전용 instance와 함께 compose에서 빠졌다.)
        #
        # 부재와 invalid를 **쪼개서** 본다. `.get()`은 둘을 `None` 하나로 뭉개는데,
        # S4가 required 집합을 좁히면 "키가 그냥 없는" 서비스가 이 자리에 도달한다 —
        # 그때 `missing or invalid`로 뭉뚱그리면 S1이 없앤 혼동을 한 층 아래에서
        # 되살리는 셈이다. 이 루프는 사실상 **두 번째 required-set**이므로 그렇게
        # 말하게 한다.
        if service_name not in services:
            raise ComposeCandidateContractError(
                "resolved compose candidate is missing required protected service: "
                + service_name
            )
        service = services.get(service_name)
        if not isinstance(service, Mapping):
            raise ComposeCandidateContractError(
                f"resolved compose candidate service {service_name} is invalid"
            )
        if service_name in _map_database_host_network_services():
            _require_map_database_host_network(service)
        service_environment = service.get("environment")
        if not isinstance(service_environment, Mapping):
            raise ComposeCandidateContractError(
                f"resolved compose candidate {service_name} has no environment mapping"
            )
        if service_name in {
            _MAP_APPLICATION_SCHEMA_SERVICE,
        }:
            _validate_map_application_300_service(
                service_name,
                service,
                environment=environment,
                resolved=True,
            )
        if service_name == _MAP_DB_ROLE_BOOTSTRAP_SERVICE:
            _validate_map_db_role_bootstrap_service(
                service_name,
                service,
                document=resolved,
                environment=environment,
                resolved=True,
            )
        if service_name == _MAP_API_SERVICE and (
            _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(service_environment)
        ):
            raise ComposeCandidateContractError(
                "resolved compose candidate Map API includes removed provider runtime environment"
            )
        if service_name == _MAP_API_SERVICE and (
            service.get("command") is not None or service.get("entrypoint") is not None
        ):
            raise ComposeCandidateContractError(
                "resolved compose candidate Map API must use the immutable image entrypoint and command"
            )
        if service_name == _MAP_UI_SERVICE and not _map_ui_auth_values_are_valid(environment):
            raise ComposeCandidateContractError(
                "resolved compose candidate Map UI authentication is invalid"
            )

    # 보호 참조와 파일 내용은 raw 단계가 설치된 릴리스 compose에서 파생한 규칙으로 봤다(ADR-51 결정 5).
    # 배선을 `.env`와 다시 대조하지 않는다 — 보간은 raw 검사와 같은 env로 돈다(결정 3). 대신 비밀 **값**이
    # 원본의 보호 참조 자리에만 있는지 본다: raw 파서와 compose가 다르게 읽거나 보간 시점에 파일 내용이
    # 들어오는 경우(`label_file` 등)의 백스톱이다. bind source 내용은 raw 단계가 같은 allowlist 경로로 봤다.
    if compose_path is not None:
        assert_resolved_secret_values_stay_at_reference_sites(
            resolved, compose_path=compose_path, environment=environment
        )
    _validate_candidate_external_resource_references(
        resolved,
        services=services,
        environment=environment,
    )
    compose_directory: Path | None = None
    root_env: Path | None = None
    if compose_path is not None and root_env_path is not None:
        try:
            compose_directory = Path(compose_path).resolve().parent
            root_env = Path(root_env_path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "resolved compose candidate source path cannot be resolved"
            ) from exc
    return _validate_candidate_volume_graph(
        resolved,
        services,
        compose_directory=compose_directory,
        root_env=root_env,
        environment=environment,
        protected_values=(),
        resolved_document=True,
    )


def validate_resolved_c6c_build_provenance(
    resolved: Mapping[str, Any],
    provenance: C6cBuildProvenance,
    *,
    expected_build_contexts: Mapping[str, str] | None = None,
) -> None:
    """production runtime build 입력이 clean checkout 파생값과 정확히 같은지 검사한다."""

    services = resolved.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("resolved compose config has no services mapping")
    # PinVi Dagster 이미지는 그것을 지금 운반하는 서비스가 빌드한다(`own`이면 webserver, 공용 plane이면
    # code-server — ADR-54). 옛 webserver는 `legacy-dagster`로 내려가 frozen render에 없다.
    pinvi_dagster_service = runtime_topology().require_service("pinvi_dagster")
    expected_provenance_args = {
        _MAP_UI_SERVICE: {
            "KOR_TRAVEL_MAP_GIT_COMMIT": provenance.map_source_revision,
        },
        _PINVI_API_SERVICE: {
            "PINVI_SOURCE_REVISION": provenance.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
        _PINVI_WEB_SERVICE: {
            "PINVI_SOURCE_REVISION": provenance.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
        pinvi_dagster_service: {
            "PINVI_SOURCE_REVISION": provenance.pinvi_source_revision,
            "PINVI_BUILD_ENVIRONMENT": "production",
        },
    }
    expected_arg_names = {
        service_name: set(args)
        for service_name, args in expected_provenance_args.items()
    }
    expected_arg_names[_MAP_UI_SERVICE].update(
        {
            "NEXT_PUBLIC_KOR_TRAVEL_MAP_API",
            "NEXT_PUBLIC_KOR_TRAVEL_MAP_DAGSTER_URL",
            "NEXT_PUBLIC_KOR_TRAVEL_GEO_BASE_URL",
            "NEXT_PUBLIC_VWORLD_API_KEY",
        }
    )
    expected_arg_names[_PINVI_WEB_SERVICE].add("NEXT_PUBLIC_PINVI_API_URL")
    expected_dockerfiles = {
        _MAP_UI_SERVICE: "docker/frontend.Dockerfile",
        _PINVI_API_SERVICE: "apps/api/Dockerfile",
        _PINVI_WEB_SERVICE: "apps/web/Dockerfile",
        pinvi_dagster_service: "apps/etl/Dockerfile",
    }
    for service_name, service_expected_args in expected_provenance_args.items():
        service = _service_mapping(services, service_name)
        build = service.get("build")
        if not isinstance(build, Mapping):
            raise DeploymentContractError(
                f"resolved compose is missing {service_name} build contract"
            )
        if set(build) != {"context", "dockerfile", "args"}:
            raise DeploymentContractError(
                f"resolved compose {service_name} build inputs are not canonical"
            )
        args = build.get("args")
        if not isinstance(args, Mapping) or set(args) != expected_arg_names[service_name]:
            raise DeploymentContractError(
                f"resolved compose {service_name} provenance build args are invalid"
            )
        for arg_name, expected_value in service_expected_args.items():
            if args.get(arg_name) != expected_value:
                raise DeploymentContractError(
                    f"resolved compose {service_name} provenance build arg is invalid"
                )
        if expected_build_contexts is None:
            continue
        expected_context = expected_build_contexts.get(service_name)
        if expected_context is None:
            raise DeploymentContractError(
                f"resolved compose {service_name} expected build context is missing"
            )
        try:
            expected_context_path = Path(expected_context).resolve(strict=True)
            context_value = build.get("context")
            if not isinstance(context_value, str) or not Path(context_value).is_absolute():
                raise ValueError("resolved build context must be absolute")
            context_path = Path(context_value).resolve(strict=True)
            dockerfile_value = build.get("dockerfile")
            if not isinstance(dockerfile_value, str):
                raise ValueError("resolved Dockerfile must be a string")
            dockerfile_path = Path(dockerfile_value)
            if not dockerfile_path.is_absolute():
                dockerfile_path = context_path / dockerfile_path
            dockerfile_path = dockerfile_path.resolve(strict=True)
            expected_dockerfile = (
                context_path / expected_dockerfiles[service_name]
            ).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise DeploymentContractError(
                f"resolved compose {service_name} build path is invalid"
            ) from exc
        if (
            context_path != expected_context_path
            or dockerfile_path != expected_dockerfile
            or not expected_dockerfile.is_file()
        ):
            raise DeploymentContractError(
                f"resolved compose {service_name} build path is not the Git snapshot"
            )


def validate_c6c_build_source_wiring(candidate: Mapping[str, Any]) -> None:
    """canonical source가 manager-derived provenance 변수만 참조하는지 검사한다."""

    services = candidate.get("services")
    if not isinstance(services, Mapping):
        raise DeploymentContractError("compose source has no services mapping")
    pinvi_dagster_service = runtime_topology().require_service("pinvi_dagster")
    expected = {
        _MAP_UI_SERVICE: {
            "context": "${KOR_TRAVEL_MAP_REPO_DIR:-../kor-travel-map}",
            "dockerfile": "docker/frontend.Dockerfile",
            "args": {
                "KOR_TRAVEL_MAP_GIT_COMMIT": "${KOR_TRAVEL_MAP_GIT_COMMIT:-development}",
                "NEXT_PUBLIC_KOR_TRAVEL_MAP_API": (
                    "${KTDM_PROD_URL_MAP_API:-http://127.0.0.1:"
                    "${KOR_TRAVEL_MAP_API_PORT:-12701}}"
                ),
                "NEXT_PUBLIC_KOR_TRAVEL_MAP_DAGSTER_URL": (
                    "${KTDM_PROD_URL_MAP_DAGSTER:-http://127.0.0.1:"
                    "${KOR_TRAVEL_MAP_DAGSTER_PORT:-12702}}"
                ),
                "NEXT_PUBLIC_KOR_TRAVEL_GEO_BASE_URL": (
                    "${KTDM_PROD_URL_GEO_API:-http://127.0.0.1:12501}"
                ),
                "NEXT_PUBLIC_VWORLD_API_KEY": "${NEXT_PUBLIC_VWORLD_API_KEY:-}",
            },
        },
        _PINVI_API_SERVICE: {
            "context": "${PINVI_REPO_DIR:-../pinvi}",
            "dockerfile": "apps/api/Dockerfile",
            "args": {
                "PINVI_SOURCE_REVISION": "${PINVI_SOURCE_REVISION:-development}",
                "PINVI_BUILD_ENVIRONMENT": "${PINVI_BUILD_ENVIRONMENT:-development}",
            },
        },
        _PINVI_WEB_SERVICE: {
            "context": "${PINVI_REPO_DIR:-../pinvi}",
            "dockerfile": "apps/web/Dockerfile",
            "args": {
                "PINVI_SOURCE_REVISION": "${PINVI_SOURCE_REVISION:-development}",
                "PINVI_BUILD_ENVIRONMENT": "${PINVI_BUILD_ENVIRONMENT:-development}",
                "NEXT_PUBLIC_PINVI_API_URL": (
                    "${PINVI_PUBLIC_API_URL:-http://127.0.0.1:12801}"
                ),
            },
        },
        pinvi_dagster_service: {
            "context": "${PINVI_REPO_DIR:-../pinvi}",
            "dockerfile": "apps/etl/Dockerfile",
            "args": {
                "PINVI_SOURCE_REVISION": "${PINVI_SOURCE_REVISION:-development}",
                "PINVI_BUILD_ENVIRONMENT": "${PINVI_BUILD_ENVIRONMENT:-development}",
            },
        },
    }
    for service_name, expected_build in expected.items():
        service = _service_mapping(services, service_name)
        build = service.get("build")
        if not isinstance(build, Mapping) or build != expected_build:
            raise DeploymentContractError(
                f"compose source {service_name} provenance build wiring is invalid"
            )


def _service_mapping(services: Mapping[str, Any], service_name: str) -> Mapping[str, Any]:
    service = services.get(service_name)
    if not isinstance(service, Mapping):
        raise DeploymentContractError(f"resolved compose is missing {service_name}")
    return service


def validate_compose_candidate_protected_values(
    candidate: Mapping[str, Any],
    *,
    compose_path: str,
    root_env_path: str,
    environment: Mapping[str, str],
    require_api_wiring: bool = True,
    external_file_contents: Mapping[str, bytes] | None = None,
) -> tuple[CandidateSystemBindSnapshot, ...]:
    """파일 반영 전 raw compose 전체의 C6c 보호 이름·값 격리를 검사한다."""

    environment = derive_curation_service_principal_environment(
        environment,
        error_type=ComposeCandidateContractError,
    )
    if require_api_wiring:
        _validate_map_production_secret_values(
            environment,
            error_type=ComposeCandidateContractError,
            reject_published_examples=(
                environment.get("KTDM_DEPLOYMENT_ENVIRONMENT") == "production"
            ),
        )
    _assert_candidate_single_file_boundary(candidate, environment=environment)
    _validate_map_database_dsn_identities(environment)
    services = candidate.get("services")
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError("compose candidate has no valid services mapping")
    # **required-set을 여섯 validator보다 먼저 본다**(GM-17 B S1).
    # 종전에는 순서가 반대였고, 그래서 서비스가 빠지면 사용자가 보는 것은 부재가
    # 아니라 "Map PostgreSQL password secret is invalid" 같은 **무관해 보이는**
    # 문구였다(S0 골든 테이블 실측: 여섯 형상 중 부재를 부재라고 말하는 것은 하나뿐).
    # 운영자는 그 메시지를 쫓다가 실제 원인에 도달하지 못한다. 집합 자체는 바꾸지
    # 않는다 — 완화는 S4의 일이고 여기서는 진단만 고친다.
    missing_services = _CANDIDATE_REQUIRED_PROTECTED_SERVICES.difference(services)
    if missing_services:
        raise ComposeCandidateContractError(
            "compose candidate is missing required protected services: "
            + ", ".join(sorted(missing_services))
        )
    # **서비스 값의 모양을 먼저 본다**(GM-17 B S1). `pinvi-api: null`은 유효한 YAML이라
    # required-set을 지나가고(키는 있다), 그 뒤 소비자 스캔이 non-Mapping을 만나
    # **어느 서비스를 null로 만들든 "Map PostgreSQL password secret is invalid"**를
    # 낸다(S0 골든 테이블 실측). 원인과 무관한 메시지다.
    #
    # 그리고 이 검사는 S4의 안전 조건이기도 하다 — 완화의 skip 판정은 **키 부재로만**
    # 해야 하는데, null을 부재로 오인하면 계약을 한 줄로 우회할 수 있다. 여기서
    # null을 "부재"가 아니라 "invalid"로 못박아 그 혼동의 여지를 없앤다.
    for service_name, service_document in services.items():
        if isinstance(service_document, Mapping):
            continue
        raise ComposeCandidateContractError(
            "compose candidate service is missing or invalid: "
            + _describe_candidate_service_key(service_name)
        )
    # ── instance admin secret : **family scope 밖의 전역 불변식** ──────────
    # 아래 전역 블록의 `_assert_instance_admin_secret_holders`는 family validator와 **다른
    # 층**이다. S4가 family scope로 게이팅하더라도 그대로 돈다 — 그것이 요점이다.
    #
    # 적대 리뷰 2026-09-17이 첫 판을 뚫었다: 전역 불변식이 Map validator **안에**
    # 있어서, 호출부를 Map PostgreSQL의 존재로 감싸는 순진한 S4가 전체 스위트 1700건을
    # 그대로 통과했고, 그 상태에서 `pinvi-api`가 Map superuser password를 마운트하는
    # 후보가 mutation 경계를 **통과**했다. ADR-53부터 그 규칙은 이름이 아니라 모든
    # PostgreSQL 서버의 admin secret에서 유도하고, 서버 형태 술어 **뒤에** 선다 — 무엇이
    # 서버인지 확정한 뒤에 그 secret을 누가 드는지 본다.
    #
    # **여기에 family 조건을 달지 마라.** 소유자가 없다고 남의 소비가 인가되지 않는다.

    # ── family scope 밖의 전역 불변식 ─────────────────────────────────────
    # **자리는 main과 같고(메시지 보존), 조건은 걸리지 않는다(S4 방어).**
    # 그 둘이 다른 축이라는 것이 적대 리뷰 2026-09-17의 정정이다 — 처음에는 "밖"을
    # 물리적 위치로 읽고 블록 앞뒤로 흩어 놓았는데, 그 탓에 1,434 형상 중 215칸의
    # 메시지가 바뀌었다. main에서 소비자 스캔은 (A)뿐 아니라 **같은 family의
    # validator 전부보다 앞**이었다.
    #
    # **이 줄들에 family 조건을 달지 마라.** 아래 family validator들이 scope로
    # 게이팅되어도 이 줄들은 그대로 돈다 — 그것이 S2·S3의 전부다.
    _assert_no_postgres_auth_override(candidate)
    # 위와 **대칭인** 전역 술어다 — 그쪽은 키의 존재를 막고 이쪽은 값을 묶는다.
    # 자리는 바로 뒤다(메시지 보존을 900형상으로 실측했다).
    _assert_canonical_postgres_initdb_args(candidate)
    # GM-17 B S3-c — 종전 한 줄을 둘로 편다. **자리는 그대로다**(감사 실측:
    # 제자리 분할은 396형상에서 메시지 변경 0칸, Map DSN 자리로 올리면 46칸이
    # 바뀌고 그중 일부는 S1의 "부재를 부재라고 말하기"를 되돌린다).
    #
    # 첫 줄은 **전역 env 불변식**이라 어떤 family 조건도 달지 않는다. 둘째 줄이
    # S4가 게이팅할 수 있는 per-service 절반이다.
    _pinvi_database_identity = _validate_pinvi_database_url_environment(environment)
    _validate_pinvi_database_url_service_identities(
        services, _pinvi_database_identity, resolved=False
    )
    # PostgreSQL 서버의 실행 형태(loopback 결박 포함)는 아래 전역 술어 하나가
    # 모든 선언·목격된 PostgreSQL에 대해 본다 — 서비스 이름을 열거하지 않는다.
    # 특권 축은 PostgreSQL보다 넓다 — 어떤 서비스도 호스트 권한을 가져갈 수
    # 없다.
    _assert_no_host_privilege_escalation(candidate)
    _assert_postgres_cluster_runtime_is_canonical(candidate)
    # 서버가 확정된 뒤다: 모든 서버의 admin secret을 누가 드는지(ADR-53).
    _assert_instance_admin_secret_holders(candidate, environment=environment, resolved=False)
    _validate_concierge_ui_canonical_contract(services, environment, resolved=False)
    _validate_map_application_300_images(services)

    # bind source·env_file **내용**이 찾을 `.env` 비밀 값은 설치된 릴리스 compose 기준으로 고른다(ADR-51 결정 5).
    protected_values = secret_values_for(compose_path=compose_path, environment=environment)

    for service_name in _candidate_protected_service_order():
        # **무조건 인덱싱하지 않는다**(GM-17 B S1). 종전 `services[service_name]`은
        # 이름이 빠지면 raw `KeyError`를 던졌고, 그것이 계약 오류가 아니라 traceback으로
        # 사용자에게 샜다.
        #
        # 이 루프의 이름은 전부 `_CANDIDATE_REQUIRED_PROTECTED_SERVICES` 안에 있다.
        # (2026-09-28까지는 `pinvi-db-init` 하나가 그 밖이었고 보증의 출처가 따로
        # 있었다 — 그 one-shot은 옛 전용 instance와 함께 compose에서 빠졌다.)
        #
        # 부재와 invalid를 **쪼개서** 본다. `.get()`은 둘을 `None` 하나로 뭉개는데,
        # S4가 required 집합을 좁히면 "키가 그냥 없는" 서비스가 이 자리에 도달한다 —
        # 그때 `missing or invalid`로 뭉뚱그리면 S1이 없앤 혼동을 한 층 아래에서
        # 되살리는 셈이다. 이 루프는 사실상 **두 번째 required-set**이므로 그렇게
        # 말하게 한다.
        if service_name not in services:
            raise ComposeCandidateContractError(
                "compose candidate is missing required protected service: " + service_name
            )
        service = services.get(service_name)
        if not isinstance(service, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate service {service_name} is invalid"
            )
        raw_environment = service.get("environment")
        if not isinstance(raw_environment, Mapping):
            if require_api_wiring or raw_environment is not None:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} must use mapping environment"
                )
            raw_environment = {}
        if service_name == _MAP_API_SERVICE and (
            _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(raw_environment)
        ):
            raise ComposeCandidateContractError(
                "compose candidate Map API includes removed provider runtime environment"
            )
        if service_name == _MAP_API_SERVICE and (
            service.get("command") is not None or service.get("entrypoint") is not None
        ):
            raise ComposeCandidateContractError(
                "compose candidate Map API must use the immutable image entrypoint and command"
            )
        if service_name in {
            _MAP_APPLICATION_SCHEMA_SERVICE,
        }:
            _validate_map_application_300_service(
                service_name,
                service,
                environment=environment,
                resolved=False,
            )
        if service_name == _MAP_DB_ROLE_BOOTSTRAP_SERVICE:
            _validate_map_db_role_bootstrap_service(
                service_name,
                service,
                document=candidate,
                environment=environment,
                resolved=False,
            )
        for allowed_service, target_name in _CANDIDATE_CANONICAL_API_ENV_VALUES:
            if allowed_service != service_name:
                continue
            if not require_api_wiring and target_name not in raw_environment:
                continue
            raw_value = raw_environment.get(target_name)
            canonical = _CANDIDATE_CANONICAL_API_ENV_VALUES[(service_name, target_name)]
            if raw_value != canonical:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name}.{target_name} wiring is invalid"
                )
        if (
            service_name == _MAP_UI_SERVICE
            and require_api_wiring
            and not _map_ui_auth_values_are_valid(environment)
        ):
            raise ComposeCandidateContractError(
                "compose candidate Map UI authentication is invalid"
            )

    # ADR-51 결정 5: 보호 참조는 설치된 릴리스 compose에서 파생한다 — 서비스 env key·다른 필드·최상위
    # 항목마다 후보의 보호 참조가 원본의 부분집합이어야 하고, `env_file`과 secret·config mount도 여기서 본다.
    # 옛 리터럴 이름 스캔의 자리다(bind·env_file 내용 검사보다 앞).
    assert_protected_references_are_derived(
        candidate, compose_path=compose_path, environment=environment
    )

    try:
        compose_directory = Path(compose_path).resolve().parent
        root_env = Path(root_env_path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate source path cannot be resolved"
        ) from exc
    _validate_candidate_external_resource_references(
        candidate,
        services=services,
        environment=environment,
    )
    # (GM-17 B S1) 같은 함수 앞머리에서 이미 같은 인자로 불렀다 — 중복 제거.
    system_bind_snapshots = _validate_candidate_volume_graph(
        candidate,
        services,
        compose_directory=compose_directory,
        root_env=root_env,
        environment=environment,
        protected_values=protected_values,
        allow_undeclared_named_volumes=not require_api_wiring,
    )
    for service_name, service in services.items():
        assert isinstance(service, Mapping)
        for env_file in _env_file_entries(service.get("env_file")):
            expanded = _expand_env_path(env_file, environment)
            resolved_path = _resolve_candidate_path(expanded, compose_directory)
            if resolved_path == root_env:
                raise ComposeCandidateContractError(
                    "compose candidate service must not load the manager root .env"
                )
            try:
                if external_file_contents is None:
                    if not resolved_path.exists():
                        continue
                    _assert_candidate_regular_file(resolved_path)
                    env_values = dotenv_values(resolved_path, interpolate=False)
                else:
                    payload = external_file_contents.get(str(resolved_path))
                    if payload is None:
                        raise ComposeCandidateContractError(
                            "compose candidate external input snapshot is incomplete"
                        )
                    env_values = dotenv_values(
                        stream=StringIO(payload.decode("utf-8")),
                        interpolate=False,
                    )
            except (OSError, UnicodeError, ValueError) as exc:
                raise ComposeCandidateContractError(
                    f"compose candidate cannot validate env_file for {service_name}"
                ) from exc
            for raw_value in env_values.values():
                text = "" if raw_value is None else str(raw_value)
                if any(value in text for value in protected_values):
                    raise ComposeCandidateContractError(
                        f"compose candidate env_file leaks C6c data for {service_name}"
                    )

    for collection_name in ("secrets", "configs"):
        collection = candidate.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} is invalid"
            )
        for _source_name, source in collection.items():
            if not isinstance(source, Mapping) or "file" not in source:
                continue
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} file resources are unsupported"
            )
    return system_bind_snapshots


def _fixture_headers(config: C6cDeploymentConfig) -> dict[str, str]:
    return {
        "X-Kor-Travel-Map-Ops-Token": config.fixture_token,
        "X-Kor-Travel-Map-Ops-Scope": "ops:fixture",
    }


def _parse_c6c_canonical_unsafe_outcome(
    value: object,
    *,
    job_id: str,
    cancellation_id: str | None,
) -> dict[str, int | str] | None:
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {"http_status", "code", "root_job_id", "cancellation_id"}
        or value.get("http_status") != 409
        or value.get("code") != "PIPELINE_CANCELLATION_UNSAFE"
        or value.get("root_job_id") != job_id
        or value.get("cancellation_id") != cancellation_id
    ):
        raise DeploymentContractError("C6c fixture canonical unsafe outcome is invalid")
    return {
        "name": "pinvi_cancel_error",
        "status": 409,
        "code": "PIPELINE_CANCELLATION_UNSAFE",
    }


def _parse_c6c_cancel_probe_fixture(
    payload: Any,
    *,
    expected_transaction_id: str,
) -> C6cCancelProbeFixture:
    if not isinstance(payload, Mapping) or set(payload) != {"data", "meta"}:
        raise DeploymentContractError("C6c fixture lifecycle envelope is invalid")
    data = payload.get("data")
    fixture = data.get("fixture") if isinstance(data, Mapping) else None
    expected_fields = {
        "transaction_id",
        "job_id",
        "state",
        "cancellation_id",
        "created_at",
        "consumed_at",
        "finalized_at",
        "canonical_unsafe_outcome",
        "capability_generation",
    }
    if (
        not isinstance(fixture, Mapping)
        or set(fixture) != expected_fields
        or fixture.get("transaction_id") != expected_transaction_id
        or not _is_uuid(fixture.get("transaction_id"))
        or not _is_uuid(fixture.get("job_id"))
        or fixture.get("state") not in {"armed", "consumed", "finalized"}
        or fixture.get("capability_generation") != C6C_CANCEL_PROBE_CAPABILITY_GENERATION
        or not _is_iso8601(fixture.get("created_at"))
        or not _is_nullable_iso8601(fixture.get("consumed_at"))
        or not _is_nullable_iso8601(fixture.get("finalized_at"))
        or not _is_nullable_uuid(fixture.get("cancellation_id"))
    ):
        raise DeploymentContractError("C6c fixture lifecycle response is invalid")
    state = cast(Literal["armed", "consumed", "finalized"], fixture["state"])
    cancellation_id = fixture.get("cancellation_id")
    consumed_at = fixture.get("consumed_at")
    finalized_at = fixture.get("finalized_at")
    if (
        (
            state == "armed"
            and (
                cancellation_id is not None
                or consumed_at is not None
                or finalized_at is not None
            )
        )
        or (
            state == "consumed"
            and (cancellation_id is None or consumed_at is None or finalized_at is not None)
        )
        or (
            state == "finalized"
            and (cancellation_id is None or consumed_at is None or finalized_at is None)
        )
    ):
        raise DeploymentContractError("C6c fixture lifecycle state is invalid")
    created_at = _parse_iso8601_datetime(fixture.get("created_at"))
    consumed_at_value = _parse_iso8601_datetime(consumed_at)
    finalized_at_value = _parse_iso8601_datetime(finalized_at)
    if (
        created_at is None
        or (consumed_at_value is not None and consumed_at_value < created_at)
        or (
            consumed_at_value is not None
            and finalized_at_value is not None
            and finalized_at_value < consumed_at_value
        )
    ):
        raise DeploymentContractError("C6c fixture lifecycle timestamp order is invalid")
    outcome = _parse_c6c_canonical_unsafe_outcome(
        fixture.get("canonical_unsafe_outcome"),
        job_id=str(fixture["job_id"]),
        cancellation_id=str(cancellation_id) if cancellation_id is not None else None,
    )
    if (state == "armed") != (outcome is None):
        raise DeploymentContractError("C6c fixture canonical outcome state is invalid")
    return C6cCancelProbeFixture(
        transaction_id=expected_transaction_id,
        job_id=str(fixture["job_id"]),
        state=state,
        cancellation_id=str(cancellation_id) if cancellation_id is not None else None,
        canonical_unsafe_outcome=outcome,
        created_at=cast(str, fixture["created_at"]),
        consumed_at=cast(str | None, consumed_at),
        finalized_at=cast(str | None, finalized_at),
    )


def _read_c6c_cancel_probe_fixture(
    config: C6cDeploymentConfig,
    transaction_id: str,
) -> C6cCancelProbeFixture:
    status, payload = _request_json(
        (
            f"{config.base_url.rstrip('/')}/v1/ops/contract-fixtures/"
            f"c6c-cancel-probe/{transaction_id}"
        ),
        method="GET",
        headers=_fixture_headers(config),
        read_error_body=True,
    )
    if status != 200:
        raise DeploymentContractError("C6c fixture lifecycle read failed")
    return _parse_c6c_cancel_probe_fixture(
        payload,
        expected_transaction_id=transaction_id,
    )


def _ensure_c6c_cancel_probe_fixture(
    config: C6cDeploymentConfig,
    state: PinviCancelProbeState,
) -> C6cCancelProbeFixture:
    transaction_id = state.transaction_id
    if not _is_uuid(transaction_id):
        raise DeploymentContractError("C6c fixture transaction ID is invalid")
    if state.fixture is not None:
        fixture = _read_c6c_cancel_probe_fixture(config, transaction_id)
        if fixture.job_id != state.fixture.job_id:
            raise DeploymentContractError("C6c fixture job identity drifted")
        state.fixture = fixture
        return fixture
    status, payload = _request_json(
        (
            f"{config.base_url.rstrip('/')}/v1/ops/contract-fixtures/"
            f"c6c-cancel-probe/{transaction_id}"
        ),
        method="PUT",
        headers=_fixture_headers(config),
        read_error_body=True,
    )
    if status != 200:
        raise DeploymentContractError("C6c fixture lifecycle ensure failed")
    fixture = _parse_c6c_cancel_probe_fixture(
        payload,
        expected_transaction_id=transaction_id,
    )
    state.fixture = fixture
    return fixture


def _finalize_c6c_cancel_probe_fixture(
    config: C6cDeploymentConfig,
    state: PinviCancelProbeState,
) -> C6cCancelProbeFixture:
    fixture = state.fixture
    if (
        fixture is None
        or fixture.state != "consumed"
        or fixture.cancellation_id is None
    ):
        raise DeploymentContractError("C6c fixture is not ready for finalization")
    status, payload = _request_json(
        (
            f"{config.base_url.rstrip('/')}/v1/ops/contract-fixtures/"
            f"c6c-cancel-probe/{fixture.transaction_id}/finalize"
        ),
        method="POST",
        headers={**_fixture_headers(config), "Content-Type": "application/json"},
        body=json.dumps({"cancellation_id": fixture.cancellation_id}).encode(),
        read_error_body=True,
    )
    if status != 200:
        raise DeploymentContractError("C6c fixture lifecycle finalization failed")
    finalized = _parse_c6c_cancel_probe_fixture(
        payload,
        expected_transaction_id=fixture.transaction_id,
    )
    if (
        finalized.state != "finalized"
        or finalized.job_id != fixture.job_id
        or finalized.cancellation_id != fixture.cancellation_id
    ):
        raise DeploymentContractError("C6c fixture finalization receipt drifted")
    state.fixture = finalized
    return finalized


def run_pinvi_canonical_smoke(
    config: C6cDeploymentConfig,
    *,
    cancel_probe_state: PinviCancelProbeState | None = None,
) -> list[dict[str, int | str]]:
    """PinVi admin session이 canonical Map read/cancel 계약을 보존하는지 검사한다."""

    smoke = config.smoke
    state = cancel_probe_state or PinviCancelProbeState()
    fixture = _ensure_c6c_cancel_probe_fixture(config, state)
    if fixture.state == "armed":
        if state.attempted:
            raise DeploymentContractError(
                "C6c destructive PinVi cancel probe cannot be repeated after an uncertain result"
            )
        if state.result is not None:
            raise DeploymentContractError("C6c armed fixture has cancellation evidence")
    else:
        if not state.attempted:
            raise DeploymentContractError(
                "C6c consumed fixture has no durable cancellation attempt"
            )
        if fixture.canonical_unsafe_outcome is None:
            raise DeploymentContractError("C6c fixture has no canonical unsafe outcome")
        if state.result is None:
            state.result = dict(fixture.canonical_unsafe_outcome)
        elif state.result != fixture.canonical_unsafe_outcome:
            raise DeploymentContractError("C6c fixture cancellation evidence drifted")

    opener = _cookie_opener(follow_redirects=False)
    login = _session_request(
        opener,
        f"{smoke.pinvi_api_base_url}/auth/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {"email": smoke.pinvi_admin_email, "password": smoke.pinvi_admin_password}
        ).encode(),
        read_error_body=False,
        retry_connection_refused=True,
    )
    if (
        login.status != 200
        or not login.set_cookie
        or not _pinvi_envelope_ok(login.payload)
    ):
        raise DeploymentContractError("C6c PinVi admin login smoke failed")

    results: list[dict[str, int | str]] = [{"name": "pinvi_login", "status": 200}]
    for name, path in (
        ("pinvi_etl_summary", "/admin/etl/summary"),
        ("pinvi_provider_sync", "/admin/provider-sync"),
    ):
        response = _session_request(
            opener,
            f"{smoke.pinvi_api_base_url}{path}",
            method="GET",
            headers={},
            read_error_body=False,
            retry_safe_get_readiness=True,
        )
        validator = (
            _validate_pinvi_etl_summary
            if name == "pinvi_etl_summary"
            else _validate_pinvi_provider_sync
        )
        if response.status != 200 or not validator(response.payload):
            raise DeploymentContractError(f"C6c {name} canonical envelope smoke failed")
        results.append({"name": name, "status": 200})

    if state.result is None:
        if state.fixture is None or state.fixture.state != "armed":
            raise DeploymentContractError("C6c fixture is not armed for PinVi cancellation")
        state.attempted = True
        cancel = _session_request(
            opener,
            (
                f"{smoke.pinvi_api_base_url}/admin/provider-sync/import-jobs/"
                f"{state.fixture.job_id}/cancel"
            ),
            method="POST",
            headers={"Content-Type": "application/json"},
            body=(
                b'{"access_reason":"c6c compatible-pair contract probe",'
                b'"kor_travel_map_reason":"c6c owned typed-failure fixture"}'
            ),
            read_error_body=True,
        )
        error = (
            cancel.payload.get("error")
            if isinstance(cancel.payload, Mapping)
            else None
        )
        error_code = error.get("code") if isinstance(error, Mapping) else None
        if not isinstance(error_code, str):
            raise DeploymentContractError(
                "C6c PinVi cancel typed error/Retry-After preservation smoke failed"
            )
        retry_after_present = (
            cancel.retry_after is not None
            if cancel.retry_after_present is None
            else cancel.retry_after_present
        )
        if (
            cancel.status != 409
            or error_code != "PIPELINE_CANCELLATION_UNSAFE"
            or retry_after_present
            or cancel.retry_after is not None
        ):
            raise DeploymentContractError(
                "C6c PinVi cancel must return exact PIPELINE_CANCELLATION_UNSAFE"
            )
        cancellation_id = _validate_owned_cancel_error_details(
            error.get("details") if isinstance(error, Mapping) else None,
            expected_status=cancel.status,
            expected_code=error_code,
            expected_root_id=state.fixture.job_id,
        )
        if cancellation_id is None:
            raise DeploymentContractError("C6c unsafe cancellation has no canonical identity")
        state.result = {
            "name": "pinvi_cancel_error",
            "status": cancel.status,
            "code": error_code,
        }
        state.fixture = _read_c6c_cancel_probe_fixture(config, state.transaction_id)
        if (
            state.fixture.state != "consumed"
            or state.fixture.cancellation_id != cancellation_id
            or state.fixture.canonical_unsafe_outcome != state.result
        ):
            raise DeploymentContractError("C6c fixture was not consumed by canonical cancellation")
    if not _validate_pinvi_cancel_probe_result(state.result):
        raise DeploymentContractError(
            "C6c cached PinVi cancel probe evidence is invalid"
        )
    if state.fixture is None:
        raise DeploymentContractError("C6c fixture receipt is missing")
    if state.fixture.state == "consumed":
        if state.finalize_attempted:
            raise DeploymentContractError(
                "C6c fixture finalization cannot be repeated after an uncertain result"
            )
        state.finalize_attempted = True
        _finalize_c6c_cancel_probe_fixture(config, state)
    elif state.fixture.state == "finalized" and not state.finalize_attempted:
        raise DeploymentContractError(
            "C6c finalized fixture has no durable finalization attempt"
        )
    if state.fixture.state != "finalized":
        raise DeploymentContractError("C6c fixture finalization is incomplete")
    assert state.result is not None
    results.append(dict(state.result))

    logout = _session_request(
        opener,
        f"{smoke.pinvi_api_base_url}/auth/logout",
        method="POST",
        headers={},
        read_error_body=False,
    )
    if logout.status != 204 or not logout.set_cookie:
        raise DeploymentContractError("C6c PinVi admin logout smoke failed")
    protected = _session_request(
        opener,
        f"{smoke.pinvi_api_base_url}/admin/provider-sync",
        method="GET",
        headers={},
        read_error_body=False,
    )
    if protected.status != 401:
        raise DeploymentContractError("C6c PinVi post-logout protection smoke failed")
    results.extend(
        [
            {"name": "pinvi_logout", "status": logout.status},
            {"name": "pinvi_post_logout_protected", "status": protected.status},
        ]
    )
    return results


def _validate_pinvi_cancel_probe_result(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"name", "status", "code"}:
        return False
    status = value.get("status")
    code = value.get("code")
    if type(status) is not int or not isinstance(code, str):
        return False
    return (
        value.get("name") == "pinvi_cancel_error"
        and (status, code) == (409, "PIPELINE_CANCELLATION_UNSAFE")
    )


def _validate_owned_cancel_error_details(
    details: Any,
    *,
    expected_status: int,
    expected_code: str,
    expected_root_id: str,
) -> str | None:
    if not isinstance(details, Mapping):
        raise DeploymentContractError("C6c PinVi cancel fixture details are missing")
    expected_attempt = {
        (409, "PIPELINE_CANCELLATION_IN_PROGRESS"): ("in_progress", False),
        (409, "PIPELINE_CANCELLATION_UNSAFE"): ("failed", False),
        (502, "DAGSTER_TERMINATE_FAILED"): ("retryable", True),
        (503, "DAGSTER_UNAVAILABLE"): ("retryable", True),
        (503, "DAGSTER_TERMINATION_TIMEOUT"): ("retryable", True),
    }.get((expected_status, expected_code))
    if expected_attempt is None:
        raise DeploymentContractError(
            "C6c PinVi cancel fixture status/code pair is unsupported"
        )
    if (
        expected_code == "PIPELINE_CANCELLATION_IN_PROGRESS"
        and set(details) == {"root", "cancellation"}
    ):
        root = details.get("root")
        if (
            not isinstance(root, Mapping)
            or set(root) != {"kind", "id"}
            or root.get("kind") != "import_job"
            or root.get("id") != expected_root_id
            or not _is_uuid(root.get("id"))
            or details.get("cancellation") is not None
        ):
            raise DeploymentContractError(
                "C6c PinVi root-only cancellation detail is invalid"
            )
        return None

    status_text, retryable = expected_attempt
    expected_fields = {
        "cancellation_id",
        "previous_cancellation_id",
        "root",
        "status",
        "requested_at",
        "requested_by",
        "reason",
        "error",
        "updated_at",
        "finished_at",
        "retryable",
        "unresolved_member_count",
        "members",
        "dagster_runs",
        "committed_data_rolled_back",
        "warnings",
    }
    root = details.get("root")
    if (
        set(details) != expected_fields
        or not isinstance(root, Mapping)
        or set(root) != {"kind", "id"}
        or root.get("kind") != "import_job"
        or root.get("id") != expected_root_id
        or details.get("status") != status_text
        or details.get("retryable") is not retryable
        or not _is_uuid(details.get("cancellation_id"))
        or not _is_nullable_uuid(details.get("previous_cancellation_id"))
        or not _is_iso8601(details.get("requested_at"))
        or not isinstance(details.get("requested_by"), str)
        or not bool(details["requested_by"])
        or not isinstance(details.get("reason"), (str, type(None)))
        or not _validate_cancellation_error(details.get("error"))
        or not _is_iso8601(details.get("updated_at"))
        or not _is_nullable_iso8601(details.get("finished_at"))
        or details.get("committed_data_rolled_back") is not False
    ):
        raise DeploymentContractError(
            "C6c PinVi cancel attempt lifecycle contract diverged"
        )
    if retryable and (
        not isinstance(details.get("error"), Mapping)
        or details["error"].get("code") != expected_code
    ):
        raise DeploymentContractError(
            "C6c retryable cancel attempt requires a structured error"
        )
    cancellation_id = str(details["cancellation_id"])
    previous_cancellation_id = details.get("previous_cancellation_id")
    finished_at = details.get("finished_at")
    attempt_error = details.get("error")
    if (
        (
            status_text == "in_progress"
            and (finished_at is not None or attempt_error is not None)
        )
        or (
            status_text in {"retryable", "failed"}
            and (finished_at is None or attempt_error is None)
        )
        or previous_cancellation_id == cancellation_id
    ):
        raise DeploymentContractError(
            "C6c PinVi cancel attempt DB lifecycle contract diverged"
        )
    unresolved = details.get("unresolved_member_count")
    members = details.get("members")
    dagster_runs = details.get("dagster_runs")
    warnings = details.get("warnings")
    unresolved_results = {"pending", "cancel_failed"}
    if not isinstance(members, list) or not all(
        _validate_cancellation_member(member) for member in members
    ):
        raise DeploymentContractError("C6c PinVi cancel members are invalid")
    if not isinstance(dagster_runs, list) or not all(
        _validate_cancellation_run(run) for run in dagster_runs
    ):
        raise DeploymentContractError("C6c PinVi cancel Dagster runs are invalid")
    member_ids = [str(member["job_id"]) for member in members]
    unresolved_count = sum(
        member.get("result") in unresolved_results for member in members
    )
    owned_members = [
        member for member in members if member.get("job_id") == expected_root_id
    ]
    run_ids = [str(run["dagster_run_id"]) for run in dagster_runs]
    member_run_ids = {
        str(member["dagster_run_id"])
        for member in members
        if member.get("dagster_run_id") is not None
    }
    if (
        not _is_nonnegative_int(unresolved)
        or not member_ids
        or len(member_ids) != len(set(member_ids))
        or (
            previous_cancellation_id is None
            and len(owned_members) != 1
        )
        or (
            previous_cancellation_id is not None
            and len(owned_members) > 1
        )
        or (
            previous_cancellation_id is not None
            and any(
                member.get("requires_run_termination") is not True
                for member in members
            )
        )
        or unresolved != unresolved_count
        or len(run_ids) != len(set(run_ids))
        or set(run_ids) != member_run_ids
        or not isinstance(warnings, list)
        or not warnings
        or not all(isinstance(item, str) for item in warnings)
    ):
        raise DeploymentContractError(
            "C6c PinVi cancel member/run/warning detail is invalid"
        )
    if status_text == "retryable" and (
        any(member.get("result") == "pending" for member in members)
        or any(run.get("result") == "pending" for run in dagster_runs)
        or not any(member.get("result") == "cancel_failed" for member in members)
    ):
        raise DeploymentContractError(
            "C6c retryable cancellation lifecycle is invalid"
        )
    run_by_id = {str(run["dagster_run_id"]): run for run in dagster_runs}
    canonical_error_codes = (
        _RETRYABLE_CANCELLATION_ERROR_CODES | _FAILED_CANCELLATION_ERROR_CODES
    )
    if status_text == "retryable":
        for member in members:
            if member.get("result") != "cancel_failed":
                continue
            run_id = member.get("dagster_run_id")
            member_error = member.get("error")
            if (
                member.get("requires_run_termination") is not True
                or not isinstance(run_id, str)
                or not isinstance(member_error, Mapping)
                or member_error.get("code")
                not in _RETRYABLE_CANCELLATION_ERROR_CODES
            ):
                raise DeploymentContractError(
                    "C6c retryable cancellation member evidence is invalid"
                )
            run = run_by_id[run_id]
            run_error = run.get("error")
            if (
                run.get("result") != "cancel_failed"
                or not isinstance(run_error, Mapping)
                or run_error.get("code")
                not in _RETRYABLE_CANCELLATION_ERROR_CODES
            ):
                raise DeploymentContractError(
                    "C6c retryable cancellation run evidence is invalid"
                )
    if status_text == "failed":
        attempt_error = details.get("error")
        if (
            not isinstance(attempt_error, Mapping)
            or attempt_error.get("code") not in _FAILED_CANCELLATION_ERROR_CODES
        ):
            raise DeploymentContractError(
                "C6c failed cancellation attempt error is invalid"
            )
        for member in members:
            if member.get("result") != "cancel_failed":
                continue
            member_error = member.get("error")
            member_error_code = (
                member_error.get("code")
                if isinstance(member_error, Mapping)
                else None
            )
            if member_error_code in _RETRYABLE_CANCELLATION_ERROR_CODES:
                run_id = member.get("dagster_run_id")
                if (
                    member.get("requires_run_termination") is not True
                    or not isinstance(run_id, str)
                    or run_by_id[run_id].get("result") != "cancel_failed"
                    or not isinstance(run_by_id[run_id].get("error"), Mapping)
                    or run_by_id[run_id]["error"].get("code")
                    not in _RETRYABLE_CANCELLATION_ERROR_CODES
                ):
                    raise DeploymentContractError(
                        "C6c failed cancellation retryable evidence is invalid"
                    )
                continue
            if member_error_code not in _FAILED_CANCELLATION_ERROR_CODES or (
                member.get("initial_status") != "running"
                and member.get("requires_run_termination") is not True
            ):
                raise DeploymentContractError(
                    "C6c failed cancellation member error is invalid"
                )
        for run in dagster_runs:
            if run.get("result") != "cancel_failed":
                continue
            run_error = run.get("error")
            if (
                not isinstance(run_error, Mapping)
                or run_error.get("code") not in canonical_error_codes
            ):
                raise DeploymentContractError(
                    "C6c failed cancellation run error is invalid"
                )
    if status_text == "in_progress":
        attempt_error = details.get("error")
        if (
            isinstance(attempt_error, Mapping)
            and attempt_error.get("code") not in canonical_error_codes
        ):
            raise DeploymentContractError(
                "C6c in-progress cancellation attempt error is invalid"
            )
        for member in members:
            if member.get("result") != "cancel_failed":
                continue
            member_error = member.get("error")
            if (
                not isinstance(member_error, Mapping)
                or member_error.get("code") not in canonical_error_codes
            ):
                raise DeploymentContractError(
                    "C6c in-progress cancellation member error is invalid"
                )
            run_id = member.get("dagster_run_id")
            member_error_code = member_error.get("code")
            if not isinstance(run_id, str):
                if member_error_code not in _FAILED_CANCELLATION_ERROR_CODES:
                    raise DeploymentContractError(
                        "C6c in-progress runless failure must be definitive"
                    )
                continue
            run = run_by_id[run_id]
            if run.get("result") in {"cancelled", "already_terminal"}:
                continue
            run_error = run.get("error")
            if (
                run.get("result") != "cancel_failed"
                or not isinstance(run_error, Mapping)
                or run_error.get("code") not in canonical_error_codes
            ):
                raise DeploymentContractError(
                    "C6c in-progress failed member has impossible run evidence"
                )
            expected_run_error_codes = (
                _RETRYABLE_CANCELLATION_ERROR_CODES
                if member_error_code in _RETRYABLE_CANCELLATION_ERROR_CODES
                else _FAILED_CANCELLATION_ERROR_CODES
            )
            if run_error.get("code") not in expected_run_error_codes:
                raise DeploymentContractError(
                    "C6c in-progress member/run failure policies must match"
                )
    success_tracking_run_ids = {
        str(member["dagster_run_id"])
        for member in members
        if member.get("dagster_run_id") is not None
        and member.get("operation_kind") == "provider_feature_load"
        and member.get("initial_status") != "done"
    }
    for member in members:
        result = str(member.get("result"))
        if result == "pending":
            continue
        if member.get("requires_run_termination") is not True:
            initial_status = member.get("initial_status")
            if initial_status == "queued" and result != "cancelled":
                raise DeploymentContractError(
                    "C6c queued cancellation requires the explicit DB cancel path"
                )
            if initial_status in {"done", "failed", "cancelled"} and (
                result != "already_terminal"
                or member.get("terminal_status") != initial_status
            ):
                raise DeploymentContractError(
                    "C6c initially terminal member must remain already-terminal"
                )
            continue
        run_id = member.get("dagster_run_id")
        if not isinstance(run_id, str):
            raise DeploymentContractError(
                "C6c run-backed cancellation member has no Dagster run"
            )
        run = run_by_id[run_id]
        if result == "cancel_failed":
            transient_terminal_run = (
                status_text == "in_progress"
                and details.get("error") is None
                and details.get("finished_at") is None
                and run.get("result") in {"cancelled", "already_terminal"}
            )
            if (
                status_text != "failed"
                and run.get("result") != "cancel_failed"
                and not transient_terminal_run
            ):
                raise DeploymentContractError(
                    "C6c run-backed failed member requires a failed run snapshot"
                )
            continue
        expected_run_terminal = {
            ("cancelled", "cancelled"): ("cancelled", "CANCELED"),
            ("already_terminal", "done"): ("already_terminal", "SUCCESS"),
            ("already_terminal", "failed"): ("already_terminal", "FAILURE"),
        }.get((result, member.get("terminal_status")))
        actual_run_terminal = (run.get("result"), run.get("terminal_status"))
        tracking_failure_after_success = (
            result == "already_terminal"
            and member.get("terminal_status") == "failed"
            and member.get("operation_kind")
            in {"provider_feature_load_run", "provider_feature_load"}
            and run_id in success_tracking_run_ids
            and actual_run_terminal == ("already_terminal", "SUCCESS")
        )
        if (
            expected_run_terminal != actual_run_terminal
            and not tracking_failure_after_success
        ):
            raise DeploymentContractError(
                "C6c resolved member status does not match Dagster terminal result"
            )
    return cancellation_id


def _validate_cancellation_error(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, Mapping)
        and set(value) == {"code", "message", "details"}
        and isinstance(value.get("code"), str)
        and bool(value["code"].strip())
        and isinstance(value.get("message"), str)
        and bool(value["message"].strip())
        and isinstance(value.get("details"), (Mapping, type(None)))
    )


def _validate_cancellation_member(value: Any) -> bool:
    expected_fields = {
        "job_id",
        "dagster_run_id",
        "operation_kind",
        "requires_run_termination",
        "initial_status",
        "result",
        "terminal_status",
        "error",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        return False
    result = value.get("result")
    terminal_status = value.get("terminal_status")
    error = value.get("error")
    dagster_run_id = value.get("dagster_run_id")
    operation_kind = value.get("operation_kind")
    initial_status = value.get("initial_status")
    expected_run_termination = dagster_run_id is not None and (
        initial_status == "running"
        or (
            initial_status == "queued"
            and operation_kind
            in {"provider_feature_load_run", "provider_feature_load"}
        )
    )
    return (
        _is_uuid(value.get("job_id"))
        and isinstance(dagster_run_id, (str, type(None)))
        and isinstance(operation_kind, (str, type(None)))
        and (
            operation_kind is None
            or (bool(operation_kind) and operation_kind == operation_kind.strip())
        )
        and value.get("requires_run_termination") is expected_run_termination
        and isinstance(initial_status, str)
        and bool(value["initial_status"])
        and result in {"pending", "cancelled", "already_terminal", "cancel_failed"}
        and isinstance(terminal_status, (str, type(None)))
        and _validate_cancellation_error(error)
        and _is_iso8601(value.get("updated_at"))
        and (
            (result == "pending" and terminal_status is None and error is None)
            or (result == "cancelled" and terminal_status == "cancelled" and error is None)
            or (
                result == "already_terminal"
                and terminal_status in {"done", "failed", "cancelled"}
                and error is None
            )
            or (result == "cancel_failed" and terminal_status is None and error is not None)
        )
    )


def _validate_cancellation_run(value: Any) -> bool:
    expected_fields = {
        "dagster_run_id",
        "initial_status",
        "termination_reserved_at",
        "result",
        "terminal_status",
        "error",
        "engine_started_at",
        "engine_finished_at",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        return False
    result = value.get("result")
    terminal_status = value.get("terminal_status")
    error = value.get("error")
    engine_started_at = value.get("engine_started_at")
    engine_finished_at = value.get("engine_finished_at")
    return (
        isinstance(value.get("dagster_run_id"), str)
        and bool(value["dagster_run_id"])
        and isinstance(value.get("initial_status"), (str, type(None)))
        and _is_nullable_iso8601(value.get("termination_reserved_at"))
        and (
            value.get("termination_reserved_at") is None
            or value.get("initial_status") is not None
        )
        and result in {"pending", "cancelled", "already_terminal", "cancel_failed"}
        and isinstance(terminal_status, (str, type(None)))
        and _validate_cancellation_error(error)
        and _is_nullable_iso8601(engine_started_at)
        and _is_nullable_iso8601(engine_finished_at)
        and _validate_cancellation_engine_times(
            result,
            engine_started_at,
            engine_finished_at,
        )
        and _is_iso8601(value.get("updated_at"))
        and (
            (result == "pending" and terminal_status is None and error is None)
            or (result == "cancelled" and terminal_status == "CANCELED" and error is None)
            or (
                result == "already_terminal"
                and terminal_status in {None, "SUCCESS", "FAILURE"}
                and error is None
            )
            or (result == "cancel_failed" and terminal_status is None and error is not None)
        )
    )


def _validate_cancellation_engine_times(
    result: Any,
    engine_started_at: Any,
    engine_finished_at: Any,
) -> bool:
    if engine_started_at is None and engine_finished_at is None:
        return True
    if result not in {"cancelled", "already_terminal"} or engine_finished_at is None:
        return False
    finished = _parse_iso8601_datetime(engine_finished_at)
    started = _parse_iso8601_datetime(engine_started_at)
    return finished is not None and (
        engine_started_at is None
        or (started is not None and started <= finished)
    )


def _pinvi_envelope_ok(payload: Any | None) -> bool:
    return isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping)


def _validate_problem(
    payload: Any | None,
    *,
    expected_status: int,
    expected_code: str,
) -> bool:
    return (
        isinstance(payload, Mapping)
        and type(payload.get("status")) is int
        and payload.get("status") == expected_status
        and payload.get("code") == expected_code
        and isinstance(payload.get("type"), str)
        and bool(payload["type"])
        and isinstance(payload.get("title"), str)
        and bool(payload["title"])
        and isinstance(payload.get("detail"), str)
        and bool(payload["detail"])
        and isinstance(payload.get("request_id"), str)
        and bool(payload["request_id"])
        and isinstance(payload.get("errors"), list)
    )


def _validate_pinvi_etl_summary(payload: Any | None) -> bool:
    if not _pinvi_envelope_ok(payload):
        return False
    if not isinstance(payload, Mapping):
        return False
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    pinvi = data.get("pinvi")
    kor_travel_map = data.get("kor_travel_map")
    status_values = {"ok", "degraded", "down", "unknown"}
    return (
        _is_iso8601(data.get("generated_at"))
        and isinstance(pinvi, Mapping)
        and pinvi.get("status") in status_values
        and all(
            _is_nonnegative_int(pinvi.get(field))
            for field in (
                "repository_count",
                "job_count",
                "asset_count",
                "schedule_count",
                "sensor_count",
            )
        )
        and all(
            isinstance(pinvi.get(field), list)
            for field in (
                "repositories",
                "recent_runs",
                "assets",
                "jobs",
                "schedules",
                "sensors",
            )
        )
        and all(_validate_dagster_run(item) for item in pinvi["recent_runs"])
        and all(_validate_dagster_repository(item) for item in pinvi["repositories"])
        and all(_validate_etl_asset(item) for item in pinvi["assets"])
        and all(_validate_etl_job(item) for item in pinvi["jobs"])
        and all(_validate_etl_schedule(item) for item in pinvi["schedules"])
        and all(_validate_etl_sensor(item) for item in pinvi["sensors"])
        and _is_nullable_iso8601(pinvi.get("checked_at"))
        and isinstance(kor_travel_map, Mapping)
        and kor_travel_map.get("status") in status_values
        and isinstance(kor_travel_map.get("dagster_status"), str)
        and _validate_nonnegative_int_mapping(kor_travel_map.get("run_counts"))
        and _validate_nonnegative_int_mapping(
            kor_travel_map.get("operations_by_status"),
            allowed_keys=_OPERATION_STATES,
        )
        and all(
            isinstance(kor_travel_map.get(field), list)
            for field in (
                "dagster_errors",
                "recent_runs",
                "recent_import_jobs",
                "errors",
            )
        )
        and all(_validate_dagster_run(item) for item in kor_travel_map["recent_runs"])
        and all(
            _validate_provider_import_job(item)
            for item in kor_travel_map["recent_import_jobs"]
        )
        and all(isinstance(item, str) for item in kor_travel_map["dagster_errors"])
        and all(isinstance(item, str) for item in kor_travel_map["errors"])
    )


def _validate_pinvi_provider_sync(payload: Any | None) -> bool:
    if not _pinvi_envelope_ok(payload):
        return False
    if not isinstance(payload, Mapping):
        return False
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    items = data.get("items")
    total = data.get("total")
    errors = data.get("schedule_source_errors")
    if (
        not isinstance(items, list)
        or not _is_nonnegative_int(total)
        or total != len(items)
        or data.get("schedule_source_status") not in {"ok", "unavailable", "error"}
        or not isinstance(errors, list)
        or not all(isinstance(error, str) for error in errors)
    ):
        return False
    for item in items:
        if (
            not isinstance(item, Mapping)
            or not all(
                isinstance(item.get(field), str) and bool(item[field])
                for field in ("provider", "dataset_key")
            )
            or not _is_canonical_sync_scope(item.get("sync_scope"))
            or item.get("status") not in _PROVIDER_SYNC_STATUSES
            or not _is_nonnegative_int(item.get("consecutive_failures"))
            or not _validate_provider_links(item.get("links"))
            or not all(
                _is_nullable_iso8601(item.get(field))
                for field in (
                    "last_success_at",
                    "last_failure_at",
                    "eligible_after",
                    "schedule_next_scheduled_at",
                )
            )
            or not isinstance(item.get("refresh_policy"), (Mapping, type(None)))
        ):
            return False
    return True


def _is_canonical_sync_scope(value: Any) -> bool:
    if value in {"dataset_wide", "target_grids"}:
        return True
    if not isinstance(value, str) or not value.startswith("external_system:"):
        return False
    external_system = value.removeprefix("external_system:")
    return (
        bool(external_system)
        and external_system == external_system.strip()
        and len(external_system) <= 112
    )


def _validate_dataset_execution(
    value: Any,
    *,
    provider: Any,
    dataset_key: Any,
    provider_dataset_id: Any,
    sync_scope: Any,
    operation_key: Any,
    active: bool,
) -> bool:
    catalog_rollup = operation_key is None
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    kind = value.get("kind")
    execution_id = value.get("id")
    operation_member_id = value.get("operation_member_id")
    execution_scope = value.get("sync_scope")
    execution_operation_key = value.get("operation_key")
    provider_datasets = value.get("provider_datasets")
    providers = value.get("providers")
    dataset_keys = value.get("dataset_keys")
    if (
        kind not in {"import_job", "update_request"}
        or not _is_uuid(execution_id)
        or value.get("detail_url")
        != f"/v1/ops/pipeline/executions/{kind}/{execution_id}"
        or value.get("status") not in _OPERATION_STATES
        or value.get("pair_status") not in _OPERATION_STATES
        or not _is_uuid(operation_member_id)
        or not _is_canonical_sync_scope(execution_scope)
        or not isinstance(execution_operation_key, str)
        or not execution_operation_key
        or (not catalog_rollup and execution_operation_key != operation_key)
        or not isinstance(providers, list)
        or not all(isinstance(item, str) and bool(item) for item in providers)
        or not isinstance(dataset_keys, list)
        or not all(isinstance(item, str) and bool(item) for item in dataset_keys)
        or not isinstance(provider_datasets, list)
        or not all(
            _validate_dataset_provider_identity(item) for item in provider_datasets
        )
        or not _is_iso8601(value.get("created_at"))
        or not all(
            _is_nullable_iso8601(value.get(field))
            for field in ("started_at", "finished_at")
        )
        or not all(
            isinstance(value.get(field), (str, type(None)))
            for field in (
                "dagster_run_id",
                "dagster_run_status",
                "trigger_kind",
                "operation_registry_version",
                "error_message",
            )
        )
        or not _validate_dataset_projected_job(value.get("projected_job"))
        or not _validate_cancellation_summary(value.get("cancellation"))
    ):
        return False
    member_keys = [
        (
            item["provider_dataset_id"],
            item["sync_scope"],
            item["operation_key"],
        )
        for item in provider_datasets
        if isinstance(item, Mapping)
    ]
    matching_members = [
        item
        for item in provider_datasets
        if isinstance(item, Mapping)
        and item.get("provider") == provider
        and item.get("dataset_key") == dataset_key
        and item.get("provider_dataset_id") == provider_dataset_id
        and item.get("sync_scope") == sync_scope
        and (
            item.get("operation_key") == execution_operation_key
            if catalog_rollup
            else item.get("operation_key") == operation_key
        )
        and item.get("operation_member_id") == operation_member_id
    ]
    allowed_pair_states = {"queued", "running"} if active else {
        "done",
        "failed",
        "cancelled",
    }
    return (
        len(member_keys) == len(set(member_keys))
        and set(providers) == {
            item["provider"] for item in provider_datasets if isinstance(item, Mapping)
        }
        and set(dataset_keys) == {
            item["dataset_key"] for item in provider_datasets if isinstance(item, Mapping)
        }
        and len(matching_members) == 1
        and execution_scope == sync_scope
        and matching_members[0].get("sync_scope") == sync_scope
        and matching_members[0].get("status") == value.get("pair_status")
        and value.get("pair_status") in allowed_pair_states
    )


def _validate_dataset_provider_identity(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("provider", "dataset_key", "sync_scope", "operation_key")
        )
        and type(value.get("provider_dataset_id")) is int
        and value["provider_dataset_id"] > 0
        and _is_canonical_sync_scope(value.get("sync_scope"))
        and _is_uuid(value.get("operation_member_id"))
        and value.get("status") in _OPERATION_STATES
    )


def _validate_dataset_projected_job(value: Any) -> bool:
    if not isinstance(value, Mapping) or not _is_uuid(value.get("id")):
        return False
    return (
        isinstance(value.get("job_kind"), str)
        and bool(value["job_kind"])
        and value.get("status") in _OPERATION_STATES
        and _is_progress(value.get("progress"))
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in (
                "current_stage",
                "error_message",
                "dagster_run_id",
                "dagster_run_status",
                "trigger_kind",
                "operation_registry_version",
            )
        )
        and _is_iso8601(value.get("created_at"))
        and all(
            _is_nullable_iso8601(value.get(field))
            for field in ("started_at", "finished_at")
        )
        and _is_nonnegative_int(value.get("depth"))
        and value.get("detail_url")
        == f"/v1/ops/pipeline/executions/import_job/{value['id']}"
    )


def _validate_dataset_catalog(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    scope_refresh = value.get("scope_refresh")
    preview = value.get("preview")
    return (
        all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("feature_kind", "provider_state_default_scope", "label")
        )
        and isinstance(value.get("is_feature_load"), bool)
        and isinstance(value.get("is_active"), bool)
        and isinstance(value.get("is_refreshable"), bool)
        and (value.get("is_refreshable") is False or value.get("is_active") is True)
        and isinstance(scope_refresh, Mapping)
        and isinstance(scope_refresh.get("supported"), bool)
        and scope_refresh.get("selector") in {"none", "poi_cache_targets"}
        and scope_refresh.get("effect") in {"none", "dataset_wide", "sync_scope"}
        and isinstance(scope_refresh.get("default_sync_scope"), str)
        and bool(scope_refresh["default_sync_scope"])
        and isinstance(scope_refresh.get("allowed_sync_scopes"), list)
        and all(
            isinstance(item, str) and _is_canonical_sync_scope(item)
            for item in scope_refresh["allowed_sync_scopes"]
        )
        and len(scope_refresh["allowed_sync_scopes"])
        == len(set(scope_refresh["allowed_sync_scopes"]))
        and isinstance(scope_refresh.get("reason"), (str, type(None)))
        and (
            (
                scope_refresh.get("selector") == "none"
                and scope_refresh.get("supported") is False
                and scope_refresh.get("effect") == "none"
                and scope_refresh.get("default_sync_scope") == "dataset_wide"
                and isinstance(scope_refresh.get("reason"), str)
                and bool(scope_refresh["reason"])
                and value.get("is_refreshable") is False
            )
            or (
                scope_refresh.get("selector") == "none"
                and scope_refresh.get("supported") is False
                and scope_refresh.get("effect") == "dataset_wide"
                and scope_refresh.get("default_sync_scope") == "dataset_wide"
                and not scope_refresh["allowed_sync_scopes"]
                and isinstance(scope_refresh.get("reason"), str)
                and bool(scope_refresh["reason"])
                and value.get("is_refreshable") is True
            )
            or (
                scope_refresh.get("selector") == "poi_cache_targets"
                and scope_refresh.get("supported") is True
                and scope_refresh.get("effect") == "sync_scope"
                and scope_refresh.get("default_sync_scope") == "target_grids"
                and bool(scope_refresh["allowed_sync_scopes"])
                and scope_refresh["allowed_sync_scopes"][0] == "target_grids"
                and scope_refresh.get("reason") is None
            )
        )
        and (
            value.get("is_refreshable") is True
            or scope_refresh.get("selector") == "none"
        )
        and isinstance(preview, Mapping)
        and isinstance(preview.get("supported"), bool)
        and isinstance(preview.get("sources"), list)
        and all(item == "fixture" for item in preview["sources"])
        and preview.get("supported") == (preview["sources"] == ["fixture"])
        and preview.get("input_kind") == "none"
        and type(preview.get("default_max_items")) is int
        and preview.get("default_max_items") == 20
        and type(preview.get("max_items_limit")) is int
        and preview.get("max_items_limit") == 100
        and type(preview.get("timeout_seconds")) in {int, float}
        and preview.get("timeout_seconds") == 5.0
        and type(preview.get("external_call_budget")) is int
        and preview.get("external_call_budget") == 0
    )


def _validate_refresh_policy(
    value: Any,
    *,
    provider: Any,
    dataset_key: Any,
) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    nullable_int_fields = (
        "system_interval_seconds",
        "optimal_interval_seconds",
        "min_interval_seconds",
        "stale_after_minutes",
        "max_requests_per_minute",
        "max_requests_per_hour",
        "max_requests_per_day",
        "burst_size",
    )
    return (
        value.get("provider") == provider
        and value.get("dataset_key") == dataset_key
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("source_kind", "targeted_policy", "config_source")
        )
        and all(_is_nullable_int(value.get(field)) for field in nullable_int_fields)
        and isinstance(value.get("max_concurrent"), int)
        and not isinstance(value.get("max_concurrent"), bool)
        and isinstance(value.get("rate_limit_source"), Mapping)
        and isinstance(value.get("enabled"), bool)
        and isinstance(value.get("revision"), str)
        and re.fullmatch(r"[1-9][0-9]*", value["revision"]) is not None
        and _is_iso8601(value.get("created_at"))
        and _is_iso8601(value.get("updated_at"))
    )


def _validate_dataset_freshness(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("state")
        in {"never_run", "fresh", "overdue", "disabled", "unknown"}
        and value.get("basis") in {"policy_stale_after", "unknown", "disabled"}
        and (
            value.get("sla_seconds") is None
            or _is_nonnegative_int(value.get("sla_seconds"))
        )
        and _is_nullable_iso8601(value.get("due_at"))
        and isinstance(value.get("is_overdue"), bool)
        and _is_nonnegative_int(value.get("overdue_by_seconds"))
    )


def _validate_dataset_schedule(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    schedule_names = value.get("schedule_names")
    active_names = value.get("active_schedule_names")
    return (
        value.get("source") == "dagster_graphql"
        and value.get("basis")
        in {"dagster_definition_tags", "not_scheduled", "unknown"}
        and isinstance(value.get("status"), (str, type(None)))
        and isinstance(schedule_names, list)
        and all(isinstance(item, str) for item in schedule_names)
        and isinstance(active_names, list)
        and all(isinstance(item, str) for item in active_names)
        and set(active_names).issubset(schedule_names)
        and _is_nullable_iso8601(value.get("next_scheduled_at"))
    )


def _validate_issue_summary(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and _is_nonnegative_int(value.get("open_count"))
        and _validate_nonnegative_int_mapping(value.get("severity_counts"))
    )


def _validate_nonnegative_int_mapping(
    value: Any,
    *,
    allowed_keys: frozenset[str] | None = None,
) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(key, str)
            and bool(key)
            and (allowed_keys is None or key in allowed_keys)
            and _is_nonnegative_int(item)
            for key, item in value.items()
        )
    )


def _validate_dagster_run(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("run_id"), str)
        and bool(value["run_id"])
        and isinstance(value.get("status"), str)
        and bool(value["status"])
        and isinstance(value.get("job_name"), (str, type(None)))
        and all(
            _is_nullable_number(value.get(field))
            for field in ("start_time", "end_time", "update_time")
        )
        and isinstance(value.get("tags"), Mapping)
    )


def _validate_dagster_repository(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    jobs = value.get("jobs")
    schedules = value.get("schedules")
    sensors = value.get("sensors")
    return (
        isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("location_name"), (str, type(None)))
        and isinstance(jobs, list)
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("is_job"), bool)
            for item in jobs
        )
        and isinstance(schedules, list)
        and all(_validate_repository_schedule(item) for item in schedules)
        and isinstance(sensors, list)
        and all(_validate_repository_sensor(item) for item in sensors)
        and _is_nonnegative_int(value.get("asset_count"))
        and isinstance(value.get("asset_groups"), list)
        and all(isinstance(item, str) for item in value["asset_groups"])
    )


def _validate_repository_schedule(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in ("job_name", "cron_schedule", "execution_timezone", "status")
        )
    )


def _validate_repository_sensor(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("status"), (str, type(None)))
    )


def _validate_etl_asset(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("key"), str)
        and bool(value["key"])
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in ("group_name", "description")
        )
    )


def _validate_etl_job(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("name", "trigger")
        )
        and isinstance(value.get("description"), (str, type(None)))
        and isinstance(value.get("asset_keys"), list)
        and all(isinstance(item, str) for item in value["asset_keys"])
    )


def _validate_etl_schedule(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in ("name", "job_name", "cron_schedule", "status")
        )
        and isinstance(value.get("execution_timezone"), (str, type(None)))
    )


def _validate_etl_sensor(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("name"), str)
        and bool(value["name"])
        and isinstance(value.get("job_name"), (str, type(None)))
        and isinstance(value.get("status"), str)
        and bool(value["status"])
    )


def _validate_provider_import_job(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        uuid.UUID(str(value.get("job_id", "")))
        uuid.UUID(str(value.get("projected_job_id", "")))
    except ValueError:
        return False
    return (
        value.get("kind") == "import_job"
        and value.get("status") in _OPERATION_STATES
        and value.get("projected_job_status") in _OPERATION_STATES
        and _is_nullable_progress(value.get("progress"))
        and _is_progress(value.get("projected_job_progress"))
        and isinstance(value.get("projected_job_kind"), str)
        and bool(value["projected_job_kind"])
        and _is_nullable_uuid(value.get("projected_job_load_batch_id"))
        and _is_nullable_uuid(value.get("projected_job_parent_job_id"))
        and _validate_cancellation_summary(value.get("cancellation"))
        and isinstance(value.get("payload"), Mapping)
        and all(
            isinstance(value.get(field), (str, type(None)))
            for field in ("status_url", "current_stage", "error_message")
        )
        and _is_iso8601(value.get("created_at"))
        and all(
            _is_nullable_iso8601(value.get(field))
            for field in ("started_at", "finished_at")
        )
        and _validate_provider_links(value.get("links"))
    )


def _validate_cancellation_summary(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    return (
        _is_uuid(value.get("cancellation_id"))
        and value.get("status")
        in {"in_progress", "retryable", "completed", "failed"}
        and _is_iso8601(value.get("requested_at"))
        and isinstance(value.get("requested_by"), str)
        and bool(value["requested_by"])
        and isinstance(value.get("reason"), (str, type(None)))
        and isinstance(value.get("retryable"), bool)
        and _is_nonnegative_int(value.get("unresolved_member_count"))
    )


def _validate_provider_links(value: Any) -> bool:
    if isinstance(value, Mapping):
        return True
    return isinstance(value, list) and all(
        isinstance(link, Mapping)
        and isinstance(link.get("rel"), str)
        and isinstance(link.get("href"), str)
        and isinstance(link.get("label"), (str, type(None)))
        for link in value
    )


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return isinstance(value, str)


def _is_nullable_uuid(value: Any) -> bool:
    return value is None or _is_uuid(value)


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nullable_int(value: Any) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _is_progress(value: Any) -> bool:
    return _is_nonnegative_int(value) and value <= 100


def _is_nullable_progress(value: Any) -> bool:
    return value is None or _is_progress(value)


def _is_nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    )


def _is_nullable_number(value: Any) -> bool:
    return value is None or _is_nonnegative_number(value)


def _is_iso8601(value: Any) -> bool:
    return _parse_iso8601_datetime(value) is not None


def _parse_iso8601_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _ISO8601_DATETIME_WITH_OFFSET.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00").replace("z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_nullable_iso8601(value: Any) -> bool:
    return value is None or _is_iso8601(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _LoopbackSmokeCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    """production Secure cookie를 TLS 종단 뒤 loopback smoke에서만 허용한다."""

    def return_ok_secure(
        self,
        cookie: http.cookiejar.Cookie,
        request: urllib.request.Request,
    ) -> bool:
        if cookie.secure and urlsplit(request.full_url).hostname == "127.0.0.1":
            return True
        return super().return_ok_secure(cookie, request)


def _cookie_opener(*, follow_redirects: bool) -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar(policy=_LoopbackSmokeCookiePolicy())
    handlers: list[Any] = [urllib.request.HTTPCookieProcessor(jar)]
    if not follow_redirects:
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


def _session_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
    read_error_body: bool,
    retry_connection_refused: bool = False,
    retry_safe_get_readiness: bool = False,
) -> HttpProbeResponse:
    if retry_safe_get_readiness and (method != "GET" or body is not None):
        raise ValueError("safe readiness retry requires a bodyless GET")
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    unavailable_message = "C6c authenticated smoke endpoint is unavailable"

    def request_once() -> HttpProbeResponse:
        try:
            with opener.open(  # noqa: S310 - production config validates loopback URLs
                request, timeout=10
            ) as response:
                raw = response.read(65_537)
                retry_after_raw = _response_header(response.headers, "Retry-After")
                return HttpProbeResponse(
                    status=response.status,
                    payload=_read_json_payload(raw),
                    retry_after=_retry_after_header(retry_after_raw),
                    retry_after_present=_has_response_header(
                        response.headers, "Retry-After"
                    ),
                    set_cookie=_has_response_header(response.headers, "Set-Cookie"),
                    location=_response_header(response.headers, "Location"),
                    body_text=_read_text_payload(raw),
                    content_type=_response_header(response.headers, "Content-Type"),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read(65_537) if read_error_body else b""
            retry_after_raw = _response_header(exc.headers, "Retry-After")
            return HttpProbeResponse(
                status=exc.code,
                payload=_read_json_payload(raw) if read_error_body else None,
                retry_after=_retry_after_header(retry_after_raw),
                retry_after_present=_has_response_header(exc.headers, "Retry-After"),
                set_cookie=_has_response_header(exc.headers, "Set-Cookie"),
                location=_response_header(exc.headers, "Location"),
                body_text=_read_text_payload(raw) if read_error_body else None,
                content_type=_response_header(exc.headers, "Content-Type"),
            )
        except OSError as exc:
            raise DeploymentContractError(unavailable_message) from exc

    if retry_connection_refused or retry_safe_get_readiness:
        return _retry_smoke_connection(
            request_once,
            unavailable_message=unavailable_message,
            attempt_count=(
                _SAFE_GET_READINESS_ATTEMPTS
                if retry_safe_get_readiness
                else LOOPBACK_HTTP_READINESS_ATTEMPTS
            ),
            retry_timeout=retry_safe_get_readiness,
        )
    return request_once()


def _retry_smoke_connection(
    operation: Callable[[], _T],
    *,
    unavailable_message: str,
    attempt_count: int | None = None,
    retry_timeout: bool = False,
) -> _T:
    """컨테이너 health 직후의 loopback 연결 race만 제한적으로 재시도한다."""

    attempt_count = (
        LOOPBACK_HTTP_READINESS_ATTEMPTS if attempt_count is None else attempt_count
    )
    if attempt_count < 1:
        raise ValueError("smoke retry attempt count must be positive")
    for attempt in range(attempt_count):
        try:
            return operation()
        except DeploymentContractError as exc:
            retry_cause: object = exc.__cause__
            if isinstance(retry_cause, urllib.error.URLError):
                retry_cause = retry_cause.reason
            if (
                str(exc) != unavailable_message
                or not isinstance(
                    retry_cause,
                    (ConnectionRefusedError, TimeoutError)
                    if retry_timeout
                    else ConnectionRefusedError,
                )
                or attempt + 1 == attempt_count
            ):
                raise
            time.sleep(LOOPBACK_HTTP_READINESS_RETRY_SECONDS)
    raise AssertionError("unreachable smoke retry state")


def _read_json_payload(raw: bytes) -> Any | None:
    if len(raw) > 65_536:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _read_text_payload(raw: bytes) -> str | None:
    if not raw or len(raw) > 65_536:
        return None
    return raw.decode("utf-8", errors="replace")


def _response_header(headers: Any | None, name: str) -> str | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get(name)
    return str(value) if value is not None else None


def _has_response_header(headers: Any | None, name: str) -> bool:
    if headers is None:
        return False
    if hasattr(headers, "get_all"):
        return bool(headers.get_all(name))
    return _response_header(headers, name) is not None


def _retry_after_header(raw: str | None) -> int | None:
    if raw is None or _ASCII_RETRY_AFTER.fullmatch(raw) is None:
        return None
    value = int(raw)
    return value if 1 <= value <= 300 else None


def _request_json(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
    read_error_body: bool = False,
) -> tuple[int, Any | None]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        # S310: production config가 exact loopback origin을 강제한다.
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            status = response.status
            if status != 200:
                return status, None
            try:
                return status, json.loads(response.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                return status, None
    except urllib.error.HTTPError as exc:
        if not read_error_body:
            # 일반 오류 body에는 upstream 진단/요청 정보가 포함될 수 있으므로 읽지 않는다.
            return exc.code, None
        raw = exc.read(65_537)
        return exc.code, _read_json_payload(raw)
    except OSError as exc:
        raise DeploymentContractError("C6c Map smoke endpoint is unavailable") from exc


def validate_current_map_ui_auth_runtime(
    runtime_config: Mapping[str, Any],
    config: C6cDeploymentConfig,
    *,
    source_env_contract_version: int = 4,
    allow_legacy_admin_proxy_absence: bool = False,
) -> None:
    """현재 Map UI 인증을 exact source 환경 계약 세대와 대조한다."""

    _validate_map_production_secrets(config)
    if source_env_contract_version not in {3, 4}:
        raise DeploymentContractError(
            "the current Map source environment contract version is unsupported"
        )
    expected = {
        _MAP_UI_USERNAME_ENV: config.smoke.map_ui_username,
        _MAP_UI_PASSWORD_HASH_ENV: config.map_ui_password_hash,
        _MAP_UI_SESSION_SECRET_ENV: config.map_ui_session_secret,
        _MAP_UI_GEO_API_KEY_ENV: config.map_geo_api_key,
        _MAP_FEATURE_CREATE_TOKEN_ENV: config.feature_create_token,
    }
    optional_expected = (
        {_MAP_ADMIN_PROXY_ENV: config.map_admin_proxy_secret}
        if source_env_contract_version == 3
        and allow_legacy_admin_proxy_absence
        else {}
    )
    if not optional_expected:
        expected[_MAP_ADMIN_PROXY_ENV] = config.map_admin_proxy_secret
    actual: dict[str, str] = {}
    allowed_paths: set[tuple[str, ...]] = set()
    plaintext = config.smoke.map_ui_password
    for env_name, value, scalar_paths in _runtime_environment_entries(
        runtime_config.get("Env")
    ):
        if env_name in _MANAGER_ONLY_CREDENTIAL_NAMES:
            raise DeploymentContractError(
                "a C6c manager-only credential is present in the current Map UI"
            )
        if plaintext and plaintext in value:
            raise DeploymentContractError(
                "the current Map UI contains a plaintext smoke credential"
            )
        if env_name not in expected and env_name not in optional_expected:
            continue
        if env_name in actual:
            raise DeploymentContractError(
                "the current Map UI has duplicate authentication variables"
            )
        actual[env_name] = value
        allowed_paths.update(scalar_paths)

    for env_name, expected_value in expected.items():
        actual_value = actual.get(env_name)
        if actual_value is None or not hmac.compare_digest(
            actual_value.encode("utf-8"), expected_value.encode("utf-8")
        ):
            raise DeploymentContractError(
                "the current Map UI authentication differs from the frozen environment"
            )
    for env_name, expected_value in optional_expected.items():
        actual_value = actual.get(env_name)
        if actual_value is not None and not hmac.compare_digest(
            actual_value.encode("utf-8"), expected_value.encode("utf-8")
        ):
            raise DeploymentContractError(
                "the current Map UI authentication differs from the frozen environment"
            )

    protected_values = (
        config.map_ui_password_hash,
        config.map_ui_session_secret,
        config.map_admin_proxy_secret,
        config.map_geo_api_key,
        config.feature_create_token,
        plaintext,
    )
    protected_names = (
        _MANAGER_ONLY_CREDENTIAL_NAMES
        | _MAP_UI_AUTH_ENV_NAMES
        | _MAP_PRODUCTION_SECRET_ENV_NAMES
        | _MAP_PRODUCTION_API_LITERAL_ENV_NAMES
        | _MAP_FEATURE_CREATE_CONTROL_ENV_NAMES
    )
    for path, scalar in _walk_scalars(runtime_config):
        if path in allowed_paths:
            continue
        text = "" if scalar is None else str(scalar)
        if any(name in text for name in protected_names) or any(
            value and value in text for value in protected_values
        ):
            raise DeploymentContractError(
                "the current Map UI authentication leaks outside its exact "
                "environment path"
            )


def validate_runtime_secret_isolation(
    container_configs: Mapping[str, Mapping[str, Any]],
    config: C6cDeploymentConfig,
) -> None:
    _validate_map_production_secrets(config)
    expected = {
        config.map_container: {
            _MAP_READ_ENV: config.read_token,
            _MAP_CANCEL_ENV: config.cancel_token,
            _MAP_FIXTURE_ENV: config.fixture_token,
            _MAP_REQUIRED_ENV: "true",
            _MAP_ADMIN_PROXY_ENV: config.map_admin_proxy_secret,
            _MAP_SERVICE_TOKEN_ENV: config.map_service_token,
            _MAP_CURSOR_SIGNING_SECRET_ENV: config.map_cursor_signing_secret,
            _MAP_GEO_API_KEY_SOURCE_ENV: config.map_geo_api_key,
            _MAP_FEATURE_CREATE_TOKEN_DIGEST_ENV: hashlib.sha256(
                config.feature_create_token.encode("utf-8")
            ).hexdigest(),
            _MAP_FEATURE_CREATE_ENABLED_ENV: config.feature_create_enabled,
            **_MAP_PRODUCTION_API_LITERAL_VALUES,
        },
        config.pinvi_container: {
            _PINVI_READ_ENV: config.read_token,
            _PINVI_CANCEL_ENV: config.cancel_token,
        },
        config.map_ui_container: {
            _MAP_UI_USERNAME_ENV: config.smoke.map_ui_username,
            _MAP_UI_PASSWORD_HASH_ENV: config.map_ui_password_hash,
            _MAP_UI_SESSION_SECRET_ENV: config.map_ui_session_secret,
            _MAP_ADMIN_PROXY_ENV: config.map_admin_proxy_secret,
            _MAP_UI_GEO_API_KEY_ENV: config.map_geo_api_key,
            _MAP_FEATURE_CREATE_TOKEN_ENV: config.feature_create_token,
        },
        **{
            container: {_MAP_GEO_API_KEY_SOURCE_ENV: config.map_geo_api_key}
            for container in _map_dagster_secret_isolation_containers()
        },
    }
    for required_container in expected:
        if required_container not in container_configs:
            raise DeploymentContractError(
                "a required C6c container is missing from runtime inspection"
            )
    secret_values = tuple(
        secret
        for secret in (
            config.read_token,
            config.cancel_token,
            config.fixture_token,
            config.map_ui_password_hash,
            config.map_ui_session_secret,
            config.map_admin_proxy_secret,
            config.map_service_token,
            config.map_cursor_signing_secret,
            config.map_geo_api_key,
            config.feature_create_token,
            hashlib.sha256(config.feature_create_token.encode("utf-8")).hexdigest(),
            config.smoke.map_ui_password,
            config.smoke.pinvi_admin_email,
            config.smoke.pinvi_admin_password,
            config.contract_generation,
        )
        if secret
    )
    protected_names = (
        _OPS_ENV_NAMES
        | _MANAGER_ONLY_CREDENTIAL_NAMES
        | _MAP_UI_AUTH_ENV_NAMES
        | _MAP_PRODUCTION_SECRET_ENV_NAMES
        | _MAP_PRODUCTION_API_LITERAL_ENV_NAMES
        | _MAP_FEATURE_CREATE_CONTROL_ENV_NAMES
    )
    for container_name, runtime_config in container_configs.items():
        if not isinstance(runtime_config, Mapping):
            raise DeploymentContractError("container returned invalid runtime config")
        allowed = expected.get(container_name, {})
        environment_entries = _runtime_environment_entries(runtime_config.get("Env"))
        environment: dict[str, str] = {}
        allowed_paths: set[tuple[str, ...]] = set()
        for env_name, value, scalar_paths in environment_entries:
            if env_name in environment:
                raise DeploymentContractError(
                    "duplicate runtime environment variables are forbidden"
                )
            environment[env_name] = value
            if env_name in _MANAGER_ONLY_CREDENTIAL_NAMES:
                raise DeploymentContractError(
                    "a C6c manager-only credential is present in a container"
                )
            if env_name in (
                _OPS_ENV_NAMES
                | _MAP_UI_AUTH_ENV_NAMES
                | _MAP_PRODUCTION_SECRET_ENV_NAMES
                | _MAP_PRODUCTION_API_LITERAL_ENV_NAMES
                | _MAP_FEATURE_CREATE_CONTROL_ENV_NAMES
            ):
                if env_name not in allowed:
                    raise DeploymentContractError(
                        "a C6c runtime protected value is present in an "
                        "unauthorized container"
                    )
                if not hmac.compare_digest(value, allowed[env_name]):
                    raise DeploymentContractError(
                        "C6c runtime protected value wiring is invalid"
                    )
                allowed_paths.update(scalar_paths)
            elif any(secret in value for secret in secret_values):
                raise DeploymentContractError(
                    "a C6c runtime secret value leaks in an unauthorized variable"
                )
        for env_name in allowed:
            if env_name not in environment:
                raise DeploymentContractError(
                    "C6c runtime protected value wiring is missing"
                )
        for path, scalar in _walk_scalars(runtime_config):
            if path in allowed_paths:
                continue
            text = "" if scalar is None else str(scalar)
            if any(name in text for name in protected_names) or any(
                secret in text for secret in secret_values
            ):
                raise DeploymentContractError(
                    "a C6c credential leaks outside its exact environment path"
                )
        if container_name == config.map_container:
            if _FORBIDDEN_MAP_API_PROVIDER_ENV_NAMES.intersection(environment):
                raise DeploymentContractError(
                    "Map API runtime includes forbidden provider environment"
                )
            if (
                "Entrypoint" not in runtime_config
                or "Cmd" not in runtime_config
                or runtime_config["Entrypoint"]
                != _MAP_API_IMMUTABLE_ENTRYPOINT
                or runtime_config["Cmd"] != _MAP_API_IMMUTABLE_COMMAND
            ):
                raise DeploymentContractError(
                    "Map API runtime must use the immutable image entrypoint and command"
                )


def _validate_image_id(image_id: str, label: str) -> None:
    if not isinstance(image_id, str) or not _IMAGE_ID_PATTERN.fullmatch(image_id):
        raise DeploymentContractError(
            f"{label} image must be an immutable sha256 image ID, not a mutable tag"
        )


def _validate_source_revision(revision: str, label: str) -> None:
    if not isinstance(revision, str) or not _SOURCE_REVISION_PATTERN.fullmatch(revision):
        raise DeploymentContractError(
            f"{label} image source revision must be an exact lowercase commit"
        )


def _run_docker_query(
    argv: list[str],
    *,
    runner: C6cCommandRunner | None,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """읽기 전용 docker 조회를 실행한다. ``runner``는 Docker 없는 검증용 주입점이다."""

    if runner is not None:
        return runner(argv)
    return subprocess.run(
        argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )


def inspect_c6c_image_source_revision(
    image_id: str,
    *,
    label: str,
    expected_build_environment: str | None = None,
    docker_bin: str = "docker",
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    runner: C6cCommandRunner | None = None,
) -> str:
    _validate_image_id(image_id, label)
    try:
        completed = _run_docker_query(
            [
                docker_bin,
                "image",
                "inspect",
                "--format={{json .Config.Labels}}",
                "--",
                image_id,
            ],
            runner=runner,
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        raise DeploymentContractError(
            f"cannot inspect {label} image source provenance"
        ) from exc
    if completed.returncode != 0:
        raise DeploymentContractError(f"cannot inspect {label} image source provenance")
    try:
        labels = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError(f"{label} image provenance labels are invalid") from exc
    if not isinstance(labels, Mapping):
        raise DeploymentContractError(f"{label} image provenance labels are missing")
    revision = labels.get("org.opencontainers.image.revision")
    if not isinstance(revision, str) or _SOURCE_REVISION_PATTERN.fullmatch(revision) is None:
        raise DeploymentContractError(f"{label} image source revision label is invalid")
    if expected_build_environment is not None and labels.get(
        "io.pinvi.build.environment"
    ) != expected_build_environment:
        raise DeploymentContractError(f"{label} image build environment label is invalid")
    return revision



def _environment_mapping(value: Any) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): "" if item is None else str(item) for key, item in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            key, _, env_value = str(item).partition("=")
            result[key] = env_value
        return result
    return {}


def _runtime_environment_entries(
    value: Any,
) -> list[tuple[str, str, set[tuple[str, ...]]]]:
    if isinstance(value, Mapping):
        return [
            (
                str(key),
                "" if item is None else str(item),
                {("Env", str(key)), ("Env", str(key), "<key>")},
            )
            for key, item in value.items()
        ]
    if isinstance(value, list):
        entries: list[tuple[str, str, set[tuple[str, ...]]]] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise DeploymentContractError("container returned invalid runtime Env")
            key, separator, env_value = item.partition("=")
            if not key or not separator:
                raise DeploymentContractError("container returned invalid runtime Env")
            entries.append((key, env_value, {("Env", str(index))}))
        return entries
    if value is None:
        return []
    raise DeploymentContractError("container returned invalid runtime Env")


def _walk_scalars(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            yield from _walk_scalars(key_text, (*path, key_text, "<key>"))
            yield from _walk_scalars(item, (*path, key_text))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_scalars(item, (*path, str(index)))
        return
    if isinstance(value, (str, int, float, bool)) or value is None:
        yield path, value


def _env_file_entries(value: Any) -> list[str]:
    entries = value if isinstance(value, list) else [value]
    result: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, Mapping) and isinstance(entry.get("path"), str):
            result.append(entry["path"])
    return result


def _expand_env_path(value: str, environment: Mapping[str, str]) -> str:
    """Compose path interpolation을 단일 단계로 해석하고 모호한 문법은 거부한다."""

    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "}":
            raise ComposeCandidateContractError(
                "compose candidate path contains unsupported interpolation"
            )
        if character != "$":
            result.append(character)
            index += 1
            continue
        if index + 1 >= len(value):
            raise ComposeCandidateContractError(
                "compose candidate path contains unresolved interpolation"
            )
        following = value[index + 1]
        if following == "$":
            result.append("$")
            index += 2
            continue
        if following == "{":
            closing = value.find("}", index + 2)
            if closing < 0:
                raise ComposeCandidateContractError(
                    "compose candidate path contains unresolved interpolation"
                )
            expression = value[index + 2 : closing]
            if "$" in expression or "{" in expression:
                raise ComposeCandidateContractError(
                    "compose candidate path contains unsupported interpolation"
                )
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-|:\?|\?|:\+|\+)(.*))?",
                expression,
            )
            if match is None:
                raise ComposeCandidateContractError(
                    "compose candidate path contains unsupported interpolation"
                )
            name, operator, word = match.groups()
            result.append(
                _resolve_compose_path_variable(
                    name,
                    operator,
                    word or "",
                    environment,
                )
            )
            index = closing + 1
            continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", value[index + 1 :])
        if match is None:
            raise ComposeCandidateContractError(
                "compose candidate path contains unsupported interpolation"
            )
        name = match.group(0)
        result.append(environment.get(name, ""))
        index += len(name) + 1
    expanded = "".join(result)
    if not expanded:
        raise ComposeCandidateContractError(
            "compose candidate path resolves to an empty value"
        )
    return expanded


def _resolve_candidate_path(value: str, compose_directory: Path) -> Path:
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = compose_directory / path
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate path cannot be resolved"
        ) from exc


def compose_volume_graph_hash(document: Mapping[str, Any]) -> str:
    services = document.get("services", {})
    if not isinstance(services, Mapping):
        raise ComposeCandidateContractError(
            "compose candidate has no valid services mapping"
        )
    graph = {
        "volumes": document.get("volumes"),
        "services": {
            str(service_name): service.get("volumes")
            for service_name, service in services.items()
            if isinstance(service, Mapping) and "volumes" in service
        },
    }
    try:
        encoded = json.dumps(
            graph,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate volume graph cannot be normalized"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _assert_candidate_single_file_boundary(
    document: Mapping[str, Any],
    *,
    environment: Mapping[str, str],
) -> None:
    if document.get("include") is not None:
        raise ComposeCandidateContractError(
            "compose candidate include is not supported by the single-file boundary"
        )
    if environment.get("COMPOSE_FILE", "").strip():
        raise ComposeCandidateContractError(
            "compose candidate COMPOSE_FILE composition is not supported"
        )
    if environment.get("KOR_TRAVEL_DOCKER_MANAGER_OVERRIDE_FILE", "").strip():
        raise ComposeCandidateContractError(
            "compose candidate override composition is not supported"
        )
    services = document.get("services")
    if not isinstance(services, Mapping):
        return
    if any(
        isinstance(service, Mapping) and service.get("extends") is not None
        for service in services.values()
    ):
        raise ComposeCandidateContractError(
            "compose candidate service extends is not supported"
        )


def revalidate_candidate_system_bind_snapshots(
    snapshots: tuple[CandidateSystemBindSnapshot, ...],
) -> None:
    current = tuple(
        _capture_candidate_system_bind_snapshot(
            service=snapshot.service,
            source=snapshot.source,
            target=snapshot.target,
            read_only=snapshot.read_only,
        )
        for snapshot in snapshots
    )
    if current != snapshots:
        raise ComposeCandidateContractError(
            "compose candidate system bind identity changed during the request"
        )


def _validate_candidate_volume_graph(
    document: Mapping[str, Any],
    services: Mapping[str, Any],
    *,
    compose_directory: Path | None,
    root_env: Path | None,
    environment: Mapping[str, str],
    protected_values: tuple[str, ...],
    allow_undeclared_named_volumes: bool = False,
    resolved_document: bool = False,
) -> tuple[CandidateSystemBindSnapshot, ...]:
    named_volumes = _candidate_named_volume_definitions(
        document.get("volumes"),
        resolved_document=resolved_document,
        compose_project_name=document.get("name"),
    )
    manager_paths: tuple[Path, ...] = ()
    if root_env is not None:
        try:
            state_paths = tuple(
                Path(path).expanduser().resolve() for path in c6c_state_paths(environment)
            )
        except (DeploymentContractError, OSError, RuntimeError, ValueError) as exc:
            raise ComposeCandidateContractError(
                "compose candidate manager path cannot be resolved"
            ) from exc
        manager_paths = (root_env, *state_paths)

    system_snapshots: list[CandidateSystemBindSnapshot] = []
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate service {service_name} is invalid"
            )
        mounts = tuple(
            _candidate_volume_mounts(
                service.get("volumes"),
                environment=environment,
            )
        )
        if str(service_name) == "cadvisor":
            _assert_candidate_cadvisor_mount_set(
                mounts,
                compose_directory=compose_directory,
                resolved_document=resolved_document,
            )
        for mount in mounts:
            if mount.kind == "volume":
                if (
                    mount.source not in named_volumes
                    and mount.source not in _CANDIDATE_ALLOWED_EXTERNAL_VOLUME_REFERENCES
                    and not allow_undeclared_named_volumes
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} named volume is undeclared"
                    )
                continue
            if compose_directory is None or root_env is None:
                raise ComposeCandidateContractError(
                    "resolved compose bind source has no canonical path context"
                )
            if _is_windows_looking_path(mount.source):
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source uses an unsupported path"
                )
            resolved_source = _resolve_candidate_path(
                mount.source,
                compose_directory,
            )
            if any(
                resolved_source == manager_path or resolved_source in manager_path.parents
                for manager_path in manager_paths
            ):
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source exposes a manager file"
                )
            if not resolved_source.exists():
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source does not exist"
                )
            system_source = _CANDIDATE_ALLOWED_SYSTEM_BINDS.get(
                (str(service_name), mount.target, mount.read_only)
            )
            if system_source is not None:
                expected_source = _resolve_candidate_path(
                    system_source,
                    compose_directory,
                )
                source_is_exact = (mount.declared_source or mount.source) == system_source
                if resolved_document:
                    source_is_exact = resolved_source == expected_source
                if not source_is_exact or resolved_source != expected_source:
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} system bind is not canonical"
                    )
                system_snapshots.append(
                    _capture_candidate_system_bind_snapshot(
                        service=str(service_name),
                        source=system_source,
                        target=mount.target,
                        read_only=mount.read_only,
                    )
                )
                continue
            expected_raw_source = registry_module.load_compose_bind_allowlist().get(
                (str(service_name), mount.target, mount.read_only)
            )
            if expected_raw_source is None:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind is not in the canonical baseline"
                )
            expected_source = _resolve_candidate_path(
                _expand_env_path(expected_raw_source, environment),
                compose_directory,
            )
            if resolved_source != expected_source:
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} bind source is not canonical"
                )
            # allowlist가 설정으로 나온 뒤에 필요해진 검사다(GM-17 A 적대 리뷰 H-1).
            # **`resolved_source`를 본다** — allowlist의 원문은 `${VAR:-...}`라
            # 리터럴만 보면 env로 어디든 가리킬 수 있다. 그리고 system bind가 위에서
            # 이미 `continue`했으므로 cadvisor의 `/sys`는 여기 오지 않는다.
            _assert_operator_bind_source_is_permitted(
                service=str(service_name), resolved_source=resolved_source
            )
            try:
                source_stat = resolved_source.stat()
            except (OSError, ValueError) as exc:
                raise ComposeCandidateContractError(
                    f"compose candidate cannot inspect {service_name} bind source"
                ) from exc
            if stat.S_ISREG(source_stat.st_mode):
                _assert_candidate_regular_file(resolved_source)
                try:
                    source_text = resolved_source.read_text(encoding="utf-8")
                except (OSError, UnicodeError, ValueError) as exc:
                    raise ComposeCandidateContractError(
                        f"compose candidate cannot validate {service_name} bind source"
                    ) from exc
                # 파일이 변수 **이름**을 적는 것은 누출이 아니다 — 스크립트는 자기가 쓰는 env 이름을 적는다.
                # 컨테이너가 그 값을 받는지는 파생 참조 규칙이 본다. 여기서는 `.env` 비밀 **값**만 찾는다
                # (ADR-51 결정 5 — 옛 Map role bootstrap 면제가 규칙이 됐다).
                if any(value in source_text for value in protected_values):
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} bind source leaks C6c data"
                    )
            elif not stat.S_ISDIR(source_stat.st_mode):
                raise ComposeCandidateContractError(
                    f"compose candidate {service_name} operator bind is not a regular file or directory"
                )
    return tuple(
        sorted(
            system_snapshots,
            key=lambda item: (item.service, item.target, item.source),
        )
    )


def _assert_candidate_cadvisor_mount_set(
    mounts: tuple[CandidateVolumeMount, ...],
    *,
    compose_directory: Path | None,
    resolved_document: bool,
) -> None:
    expected_sources = {
        "/sys": "/sys",
        "/var/run/docker.sock": "/var/run/docker.sock",
    }
    expected = {
        ("bind", source, target, True)
        for source, target in expected_sources.items()
    }
    if resolved_document:
        if compose_directory is None:
            raise ComposeCandidateContractError(
                "resolved cAdvisor mounts have no canonical path context"
            )
        expected = {
            (
                "bind",
                str(_resolve_candidate_path(source, compose_directory)),
                target,
                True,
            )
            for source, target in expected_sources.items()
        }
    actual: set[tuple[str, str, str, bool]] = set()
    for mount in mounts:
        source = mount.declared_source or mount.source
        target = mount.declared_target or mount.target
        if resolved_document and mount.kind == "bind":
            assert compose_directory is not None
            source = str(_resolve_candidate_path(mount.source, compose_directory))
            target = mount.target
        actual.add((mount.kind, source, target, mount.read_only))
    if actual != expected or len(mounts) != len(expected):
        raise ComposeCandidateContractError(
            "compose candidate cAdvisor mounts must be exactly read-only /sys and Docker socket"
        )


def _candidate_named_volume_definitions(
    value: Any,
    *,
    resolved_document: bool,
    compose_project_name: Any,
) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, Mapping):
        raise ComposeCandidateContractError(
            "compose candidate top-level volumes must be a mapping"
        )
    if resolved_document and (
        not isinstance(compose_project_name, str)
        or not _COMPOSE_PROJECT_PATTERN.fullmatch(compose_project_name)
    ):
        raise ComposeCandidateContractError(
            "resolved compose named volumes require a canonical project name"
        )
    names: set[str] = set()
    for raw_name, definition in value.items():
        name = str(raw_name)
        if not _is_named_volume_source(name):
            raise ComposeCandidateContractError(
                "compose candidate named volume has an invalid name"
            )
        if definition is None:
            if resolved_document:
                raise ComposeCandidateContractError(
                    f"resolved compose named volume {name} name is not canonical"
                )
            names.add(name)
            continue
        if not isinstance(definition, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} has an invalid definition"
            )
        allowed_keys = {"driver", "driver_opts"}
        if resolved_document:
            allowed_keys.update({"external", "name"})
        if set(definition) - allowed_keys:
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} has unsupported options"
            )
        driver = definition.get("driver")
        if driver is not None and driver != "local":
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} has an unsupported driver"
            )
        driver_opts = definition.get("driver_opts")
        if driver_opts is not None and (
            not isinstance(driver_opts, Mapping) or bool(driver_opts)
        ):
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} driver options are not allowed"
            )
        external = definition.get("external")
        if external is not None and external is not False:
            raise ComposeCandidateContractError(
                f"compose candidate named volume {name} cannot be external"
            )
        resolved_name = definition.get("name")
        if resolved_document:
            expected_name = f"{compose_project_name}_{name}"
            if resolved_name != expected_name:
                raise ComposeCandidateContractError(
                    f"resolved compose named volume {name} name is not canonical"
                )
        names.add(name)
    return frozenset(names)


def _candidate_volume_mounts(
    value: Any,
    *,
    environment: Mapping[str, str],
) -> Iterable[CandidateVolumeMount]:
    if value is None:
        return
    if not isinstance(value, list):
        raise ComposeCandidateContractError(
            "compose candidate service volumes must be a list"
        )
    for entry in value:
        if isinstance(entry, str):
            declared_source: str | None = None
            declared_target: str | None = None
            declared_mount = entry
            declared_mode = declared_mount.rpartition(":")[2]
            if declared_mode in {"ro", "rw"}:
                declared_mount = declared_mount.rpartition(":")[0]
            if ":" in declared_mount:
                declared_source, _, declared_target = declared_mount.rpartition(":")
            expanded = _expand_env_path(entry, environment)
            if re.match(r"^[A-Za-z]:", expanded) or expanded.startswith("\\\\"):
                raise ComposeCandidateContractError(
                    "compose candidate bind source uses an unsupported Windows path"
                )
            parts = expanded.split(":")
            if len(parts) == 1:
                raise ComposeCandidateContractError(
                    "compose candidate anonymous volume is not allowed"
                )
            if len(parts) not in {2, 3} or not parts[0]:
                raise ComposeCandidateContractError(
                    "compose candidate short volume syntax is ambiguous"
                )
            source = parts[0]
            target = parts[1]
            if not target:
                raise ComposeCandidateContractError(
                    "compose candidate short volume target is empty"
                )
            mode = parts[2] if len(parts) == 3 else "rw"
            if mode not in {"ro", "rw"}:
                raise ComposeCandidateContractError(
                    "compose candidate short volume mode is not allowed"
                )
            kind = "volume" if _is_named_volume_source(source) else "bind"
            yield CandidateVolumeMount(
                kind=kind,
                source=source,
                target=target,
                read_only=mode == "ro",
                declared_source=declared_source,
                declared_target=declared_target,
            )
            continue
        if not isinstance(entry, Mapping):
            raise ComposeCandidateContractError(
                "compose candidate volume entry is invalid"
            )
        raw_type = entry.get("type")
        if not isinstance(raw_type, str):
            raise ComposeCandidateContractError(
                "compose candidate long volume has no valid type"
            )
        volume_type = raw_type.strip().lower()
        if volume_type not in {"bind", "volume"}:
            raise ComposeCandidateContractError(
                "compose candidate long volume type is not allowed"
            )
        if "source" in entry and "src" in entry:
            raise ComposeCandidateContractError(
                "compose candidate long volume source is ambiguous"
            )
        if "target" in entry and "dst" in entry:
            raise ComposeCandidateContractError(
                "compose candidate long volume target is ambiguous"
            )
        raw_source = entry.get("source", entry.get("src"))
        raw_target = entry.get("target", entry.get("dst"))
        if not isinstance(raw_source, str):
            raise ComposeCandidateContractError(
                "compose candidate bind volume has no valid source"
            )
        if not isinstance(raw_target, str) or not raw_target:
            raise ComposeCandidateContractError(
                "compose candidate bind volume has no valid target"
            )
        read_only = entry.get("read_only", False)
        if type(read_only) is not bool:
            raise ComposeCandidateContractError(
                "compose candidate bind read_only must be a boolean"
            )
        common_keys = {"type", "source", "src", "target", "dst", "read_only"}
        extra_keys = set(entry) - common_keys
        option_name = "bind" if volume_type == "bind" else "volume"
        if extra_keys - {option_name}:
            raise ComposeCandidateContractError(
                "compose candidate long volume has unsupported options"
            )
        options = entry.get(option_name)
        if options is not None:
            if not isinstance(options, Mapping):
                raise ComposeCandidateContractError(
                    "compose candidate long volume options are invalid"
                )
            allowed_options = (
                {"create_host_path"} if volume_type == "bind" else set()
            )
            if set(options) - allowed_options:
                raise ComposeCandidateContractError(
                    "compose candidate long volume options are not allowed"
                )
            if "create_host_path" in options and options["create_host_path"] is not True:
                raise ComposeCandidateContractError(
                    "compose candidate bind create_host_path is invalid"
                )
        source = _expand_env_path(raw_source, environment)
        target = _expand_env_path(raw_target, environment)
        if volume_type == "volume" and not _is_named_volume_source(source):
            raise ComposeCandidateContractError(
                "compose candidate named volume source is invalid"
            )
        yield CandidateVolumeMount(
            kind=volume_type,
            source=source,
            target=target,
            read_only=read_only,
            declared_source=raw_source,
            declared_target=raw_target,
        )


def _capture_candidate_system_bind_snapshot(
    *,
    service: str,
    source: str,
    target: str,
    read_only: bool,
) -> CandidateSystemBindSnapshot:
    try:
        raw_path = Path(source)
        if raw_path.is_symlink():
            raise ComposeCandidateContractError(
                f"compose candidate {service} system bind cannot be a symlink"
            )
        resolved = raw_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ComposeCandidateContractError(
            f"compose candidate {service} system bind does not exist"
        ) from exc
    try:
        source_stat = resolved.stat()
    except (OSError, ValueError) as exc:
        raise ComposeCandidateContractError(
            f"compose candidate cannot inspect {service} system bind"
        ) from exc
    if source == "/sys":
        if resolved != Path("/sys") or not stat.S_ISDIR(source_stat.st_mode):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor /sys bind is not the expected directory"
            )
        if not os.path.ismount(resolved):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor /sys bind is not a mountpoint"
            )
    elif source == "/var/run/docker.sock":
        if not stat.S_ISSOCK(source_stat.st_mode):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor Docker source is not a socket"
            )
        try:
            docker_gid = grp.getgrnam("docker").gr_gid
        except KeyError as exc:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket group cannot be verified"
            ) from exc
        if source_stat.st_gid != docker_gid:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket is not owned by the docker group"
            )
        if source_stat.st_mode & stat.S_IWOTH:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket is world-writable"
            )
        if stat.S_IMODE(source_stat.st_mode) != 0o660:
            raise ComposeCandidateContractError(
                "compose candidate Docker socket mode is not root:docker 0660"
            )
    else:
        raise ComposeCandidateContractError(
            f"compose candidate {service} system bind is not allowed"
        )
    if source_stat.st_uid != 0:
        raise ComposeCandidateContractError(
            f"compose candidate {service} system bind is not root-owned"
        )

    chain: list[CandidatePathIdentity] = []
    current = resolved
    first = True
    while True:
        try:
            current_stat = current.stat()
        except (OSError, ValueError) as exc:
            raise ComposeCandidateContractError(
                f"compose candidate cannot inspect {service} system bind parent"
            ) from exc
        if current_stat.st_uid != 0:
            raise ComposeCandidateContractError(
                f"compose candidate {service} system bind chain is not root-owned"
            )
        if not first and current_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ComposeCandidateContractError(
                f"compose candidate {service} system bind parent is writable"
            )
        if first and source == "/sys" and current_stat.st_mode & (
            stat.S_IWGRP | stat.S_IWOTH
        ):
            raise ComposeCandidateContractError(
                "compose candidate cAdvisor /sys source is writable"
            )
        chain.append(
            CandidatePathIdentity(
                path=str(current),
                device=current_stat.st_dev,
                inode=current_stat.st_ino,
                mode=current_stat.st_mode,
                uid=current_stat.st_uid,
                gid=current_stat.st_gid,
            )
        )
        parent = current.parent
        if parent == current:
            break
        current = parent
        first = False
    return CandidateSystemBindSnapshot(
        service=service,
        source=source,
        target=target,
        read_only=read_only,
        path_chain=tuple(chain),
    )


def _is_named_volume_source(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is not None


def _is_windows_looking_path(value: str) -> bool:
    return (
        re.match(r"^[A-Za-z]:", value) is not None
        or value.startswith("\\\\")
        or value.startswith("//")
        or "\\" in value
    )


def _validate_candidate_external_resource_references(
    document: Mapping[str, Any],
    *,
    services: Mapping[str, Any],
    environment: Mapping[str, str],
) -> None:
    """secret·config 선언의 모양과 external alias의 소비를 본다.

    `environment:`가 어느 변수를 가리켜도 되는지는 여기서 보지 않는다 — 설치된 릴리스 compose의 같은 자리와
    대조하는 파생 규칙이 본다(ADR-51 결정 5). 종전의 이름 스캔은 정상 선언 셋만 하드코딩으로 면제했다.
    """
    for collection_name in ("secrets", "configs"):
        collection = document.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, Mapping):
            raise ComposeCandidateContractError(
                f"compose candidate top-level {collection_name} is invalid"
            )
        external_aliases: set[str] = set()
        for alias, source in collection.items():
            if not isinstance(source, Mapping):
                continue
            environment_name = source.get("environment")
            if environment_name is not None:
                if not isinstance(environment_name, str):
                    raise ComposeCandidateContractError(
                        f"compose candidate {collection_name}.{alias} environment is invalid"
                    )
                if environment.get(environment_name) is None:
                    raise ComposeCandidateContractError(
                        f"compose candidate {collection_name}.{alias} environment is unresolved"
                    )
            external = source.get("external")
            is_external = external is not None and external is not False
            has_uninspectable_name = "name" in source and not any(
                key in source for key in ("file", "content", "environment")
            )
            if is_external or has_uninspectable_name:
                external_aliases.add(str(alias))

        if not external_aliases:
            continue
        for service_name, service in services.items():
            if not isinstance(service, Mapping):
                raise ComposeCandidateContractError(
                    f"compose candidate service {service_name} is invalid"
                )
            for alias in _candidate_resource_references(
                service.get(collection_name),
                collection_name=collection_name,
            ):
                placement = (str(service_name), collection_name, alias)
                if (
                    alias in external_aliases
                    and placement
                    not in _CANDIDATE_ALLOWED_EXTERNAL_RESOURCE_REFERENCES
                ):
                    raise ComposeCandidateContractError(
                        f"compose candidate {service_name} uses uninspectable external {collection_name}"
                    )


def _candidate_resource_references(
    value: Any,
    *,
    collection_name: str,
) -> Iterable[str]:
    if value is None:
        return
    if not isinstance(value, list):
        raise ComposeCandidateContractError(
            f"compose candidate service {collection_name} must be a list"
        )
    for entry in value:
        if isinstance(entry, str):
            yield entry
            continue
        if not isinstance(entry, Mapping) or not isinstance(
            entry.get("source"), str
        ):
            raise ComposeCandidateContractError(
                f"compose candidate service {collection_name} reference is invalid"
            )
        yield entry["source"]


def _assert_candidate_regular_file(path: Path) -> None:
    try:
        file_stat = path.stat()
    except (OSError, ValueError) as exc:
        raise ComposeCandidateContractError(
            "compose candidate external file cannot be inspected"
        ) from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_size > _CANDIDATE_EXTERNAL_FILE_MAX_BYTES
    ):
        raise ComposeCandidateContractError(
            "compose candidate external file must be a bounded regular file"
        )


def _resolve_compose_path_variable(
    name: str,
    operator: str | None,
    word: str,
    environment: Mapping[str, str],
) -> str:
    is_set = name in environment
    current = environment.get(name, "")
    is_nonempty = bool(current)
    if operator is None:
        return current
    if operator == ":-":
        return current if is_set and is_nonempty else word
    if operator == "-":
        return current if is_set else word
    if operator == ":+":
        return word if is_set and is_nonempty else ""
    if operator == "+":
        return word if is_set else ""
    if operator == ":?" and (not is_set or not is_nonempty):
        raise ComposeCandidateContractError(
            "compose candidate path requires a non-empty environment value"
        )
    if operator == "?" and not is_set:
        raise ComposeCandidateContractError(
            "compose candidate path requires a configured environment value"
        )
    return current


# GM-20: 이 모듈이 재수출만 하고 내부에서는 쓰지 않는 이름을 ruff의 미사용 import
# 경고에서 제외한다(metrics_collector.py:971의 3-이름 __all__과 같은 관례 —
# 이 거대 모듈의 다른 공개 이름 전부를 여기 나열할 필요는 없다).
__all__ = [
    "ComposeCandidateContractError",
    "ComposePostMutationContractError",
    "DeploymentContractError",
    "_MANAGED_COMPOSE_MUTATION_CAPABILITY",
    "_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY",
]
