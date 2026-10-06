"""C6c 보호 참조를 리터럴 표 대신 파생 규칙으로 판정한다(ADR-51 결정 5).

UI·API 사용자는 git 리뷰 없이 compose를 고친다 — in-model 행위자다. 그 후보가 한 자리(서비스의
env key, 서비스의 다른 필드, 최상위 항목)에서 참조하는 **보호 변수**는 설치된 릴리스 compose가 같은
자리에서 참조하는 것의 부분집합이어야 한다.

- 보호 변수: 이름이 민감하거나(`is_sensitive_key`) 값이 `.env` 비밀을 **담는** 변수(DSN처럼).
- `.env` 비밀: 민감한 이름의 값 중 4자 이상이고 원본 compose 텍스트에 나타나지 않는 값. 원본에 적힌
  기본값(`${X:-admin}`)이 비밀로 취급되면 설치된 compose 자체가 거부된다.
- 서비스의 secret·config mount는 그 항목의 `environment:` 변수를 참조한 것으로, 값 없는 env key(`KEY:`,
  목록의 `KEY`)는 같은 이름의 변수를 참조한 것으로(compose가 환경에서 끌어온다), `env_file` 항목은 언제나
  보호된 참조로 센다.

원본은 설치기가 release에 남기는 `.ktdm-release-compose.yml`이다(UI는 `docker-compose.yml`을 제자리에서
고친다). release가 아닌 개발 checkout에서는 compose 파일 자체가 git이 추적하는 원본이다.
"""

from __future__ import annotations

import string
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from kor_travel_docker_manager.services.errors import ComposeCandidateContractError
from kor_travel_docker_manager.services.secret_scrub import is_sensitive_key
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

RELEASE_COMPOSE_NAME = ".ktdm-release-compose.yml"
_RELEASE_MARKER = ".ktdm-source-revision"
_MINIMUM_SECRET_LENGTH = 4
#: compose 변수 이름에 쓰이는 글자. `str.isalnum()`은 비ASCII 글자도 받아 `$DSNé`를 다른 이름으로 읽었다 —
#: compose는 `DSN`을 치환하고 `é`를 글자로 남긴다(적대 리뷰 D5 P-2 H-1).
_NAME_CHARACTERS = frozenset(string.ascii_letters + string.digits + "_")

#: 참조가 놓인 자리. 서비스 env는 key까지, 서비스의 다른 필드는 필드까지, 최상위 항목은 이름까지 본다 —
#: 목록 순서를 바꿨다고 자리가 달라지면 안 된다.
Site = tuple[str, ...]


def reference_compose_path(compose_path: str | Path) -> Path:
    root = Path(compose_path).parent
    reference = root / RELEASE_COMPOSE_NAME
    if reference.is_file():
        return reference
    if (root / _RELEASE_MARKER).exists():
        raise ComposeCandidateContractError(
            "the installed Manager release has no reference compose "
            f"({RELEASE_COMPOSE_NAME}); rerun the installer for this revision — it rewrites the copy from git"
        )
    return Path(compose_path)


def variable_names(text: str) -> set[str]:
    """`${NAME...}`·`$NAME`이 가리키는 변수 이름. `$$`는 escape다. 기본값 안의 참조도 센다."""

    names: set[str] = set()
    index = 0
    while index < len(text):
        if text[index] != "$":
            index += 1
            continue
        following = text[index + 1 : index + 2]
        if following == "$":
            index += 2
            continue
        start = index + 2 if following == "{" else index + 1
        end = start
        while end < len(text) and text[end] in _NAME_CHARACTERS:
            end += 1
        name = text[start:end]
        if name and not name[0].isdigit():
            names.add(name)
        index = max(end, index + 1)
    return names


def _scalars(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _scalars(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scalars(item)
    elif isinstance(value, str):
        yield value


def _sited_scalars(value: Any, site: Site = ()) -> Iterable[tuple[Site, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield (*site, str(key)), str(key)
            yield from _sited_scalars(item, (*site, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _sited_scalars(item, (*site, str(index)))
    elif isinstance(value, str):
        yield site, value


def _environment_references(value: Any) -> Iterable[tuple[str, set[str]]]:
    """env key마다 참조하는 변수. 값이 없으면(`KEY:`·목록의 `KEY`) compose가 같은 이름을 환경에서 끌어온다."""

    if isinstance(value, Mapping):
        pairs = [(str(key), None if item is None else str(item)) for key, item in value.items()]
    elif isinstance(value, list):
        pairs = []
        for entry in value:
            key, separator, item = str(entry).partition("=")
            pairs.append((key, item if separator else None))
    else:
        return
    for key, item in pairs:
        yield key, variable_names(key) | ({key} if item is None else variable_names(item))


def _mount_aliases(value: Any) -> Iterable[str]:
    for entry in value if isinstance(value, list) else [value]:
        if isinstance(entry, str):
            yield entry
        elif isinstance(entry, Mapping) and isinstance(entry.get("source"), str):
            yield entry["source"]


def _env_file_paths(value: Any) -> Iterable[str]:
    for entry in value if isinstance(value, list) else [value]:
        if isinstance(entry, str):
            yield entry
        elif isinstance(entry, Mapping) and isinstance(entry.get("path"), str):
            yield entry["path"]


def compose_references(document: Mapping[str, Any]) -> dict[Site, set[str]]:
    """자리마다 참조하는 변수 이름. `env_file` 항목은 `env_file:<path>`로 센다."""

    references: dict[Site, set[str]] = {}

    def add(site: Site, names: Iterable[str]) -> None:
        collected = set(names)
        if collected:
            references.setdefault(site, set()).update(collected)

    mountable = {
        section: block
        for section in ("secrets", "configs")
        if isinstance(block := document.get(section), Mapping)
    }
    services = document.get("services")
    for service, service_document in (services if isinstance(services, Mapping) else {}).items():
        if not isinstance(service_document, Mapping):
            continue
        for field, value in service_document.items():
            site: Site = ("services", str(service), str(field))
            if field == "environment":
                for key, names in _environment_references(value):
                    add((*site, key), names)
                continue
            if field in mountable:
                for alias in _mount_aliases(value):
                    declared = mountable[field].get(alias)
                    if isinstance(declared, Mapping) and isinstance(declared.get("environment"), str):
                        add(site, {declared["environment"]})
            if field == "env_file":
                add(site, {f"env_file:{path}" for path in _env_file_paths(value)})
                continue
            add(site, (name for scalar in _scalars(value) for name in variable_names(scalar)))
    for section, block in document.items():
        if section == "services":
            continue
        if isinstance(block, Mapping):
            for name, item in block.items():
                add(
                    (str(section), str(name)),
                    (found for scalar in _scalars(item) for found in variable_names(scalar)),
                )
        else:
            add((str(section),), (found for scalar in _scalars(block) for found in variable_names(scalar)))
    return references


def secret_values(environment: Mapping[str, str], *, reference_text: str) -> set[str]:
    """`.env` 비밀. 불리언·숫자는 이름이 민감해도 비밀이 아니다 — `..._TOKEN_TTL_S=3600`이 비밀이면
    모든 `3600`이 거부된다."""

    return {
        value
        for key, value in environment.items()
        if value
        and len(value) >= _MINIMUM_SECRET_LENGTH
        and is_sensitive_key(key)
        and value.lower() not in {"true", "false"}
        and not value.isdigit()
        and value not in reference_text
    }


def _is_protected(name: str, environment: Mapping[str, str], secret_set: set[str]) -> bool:
    if name.startswith("env_file:") or is_sensitive_key(name):
        return True
    value = environment.get(name) or ""
    # DSN은 비밀번호를 percent-encoding으로 담을 수 있다 — 풀어서도 본다.
    candidates = (value, unquote(value))
    return any(secret in text for text in candidates for secret in secret_set)


def _describe(site: Site) -> str:
    return ".".join(site[1:] if site[:1] == ("services",) else site)


def _load_reference(compose_path: str | Path) -> tuple[str, Mapping[str, Any]]:
    reference_path = reference_compose_path(compose_path)
    try:
        reference_text = reference_path.read_text(encoding="utf-8")
        reference = load_yaml_rejecting_duplicate_keys(reference_text)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
        raise ComposeCandidateContractError(
            f"the reference compose cannot be read: {reference_path}"
        ) from error
    if not isinstance(reference, Mapping):
        raise ComposeCandidateContractError(f"the reference compose is not a mapping: {reference_path}")
    return reference_text, reference


def secret_values_for(*, compose_path: str | Path, environment: Mapping[str, str]) -> tuple[str, ...]:
    """bind source·`env_file` **내용** 스캔이 찾을 `.env` 비밀 값(긴 것부터).

    원본 compose 텍스트에 적힌 값은 비밀이 아니다(`secret_values`). 이름은 찾지 않는다 — 파일이 변수
    이름을 적는 것은 누출이 아니다.
    """

    reference_text, _reference = _load_reference(compose_path)
    return tuple(sorted(secret_values(environment, reference_text=reference_text), key=len, reverse=True))


def _checkout_anchors(service_document: Mapping[str, Any]) -> set[str]:
    """서비스의 build context와 `env_file` 경로가 뿌리를 두는 변수(대개 `*_REPO_DIR`)."""

    build = service_document.get("build")
    context = build.get("context") if isinstance(build, Mapping) else build
    anchors = variable_names(context) if isinstance(context, str) else set()
    for path in _env_file_paths(service_document.get("env_file")):
        anchors |= variable_names(path)
    return anchors


def env_file_secret_values_by_service(
    *, compose_path: str | Path, environment: Mapping[str, str]
) -> dict[str, tuple[str, ...]]:
    """`env_file` **내용**이 담으면 누출인 `.env` 비밀 값을 서비스마다(긴 것부터).

    `env_file`은 원본 compose가 고정한 자리에만 있다(`assert_protected_references_are_derived`). 그 파일은
    한 소스 checkout 안에 산다 — 그 checkout에서 빌드하거나 같은 checkout에 `env_file`을 둔 원본의 서비스들이
    그 파일의 **가족**이다. 원본이 그 가족에게 이미 주는 값(가족 서비스의 어느 자리든 참조하는 변수의 값, DSN 안의
    비밀 포함)은 파일에 다시 있어도 새로 흘러가는 것이 없다. 그 밖의 `.env` 비밀(다른 프로젝트의 DB password,
    proxy secret, token)만 누출이다. 이름이 아니라 값으로 판정한다 — 여러 서비스가 같은 값을 공유하면(VWorld
    browser key) 원본이 가족에게 그 값을 준 순간 그 값은 가족의 것이다.

    원본에 `env_file`이 없는 서비스는 결과에 없다. 호출부는 그 경우 전체 비밀 집합을 쓴다(후보가 원본에 없는
    `env_file`을 추가하면 참조 규칙이 먼저 거부한다).
    """

    reference_text, reference = _load_reference(compose_path)
    secret_set = secret_values(environment, reference_text=reference_text)
    services = reference.get("services")
    if not isinstance(services, Mapping):
        return {}
    documents = {
        str(name): document for name, document in services.items() if isinstance(document, Mapping)
    }
    granted_names: dict[str, set[str]] = {}
    for site, names in compose_references(reference).items():
        if site[:1] == ("services",) and site[2:3] != ("env_file",):
            granted_names.setdefault(site[1], set()).update(names)
    anchors = {name: _checkout_anchors(document) for name, document in documents.items()}
    result: dict[str, tuple[str, ...]] = {}
    for name, document in documents.items():
        paths = list(_env_file_paths(document.get("env_file")))
        if not paths:
            continue
        own = set().union(*(variable_names(path) for path in paths))
        family = {name} | {other for other, other_anchors in anchors.items() if own & other_anchors}
        granted_texts = [
            text
            for member in family
            for variable in granted_names.get(member, ())
            for value in [environment.get(variable) or ""]
            for text in (value, unquote(value))
            if text
        ]
        result[name] = tuple(
            sorted(
                (secret for secret in secret_set if not any(secret in text for text in granted_texts)),
                key=len,
                reverse=True,
            )
        )
    return result


def _site_of(path: tuple[str, ...]) -> Site:
    if path[:1] == ("services",) and len(path) >= 3:
        if path[2] == "environment" and len(path) >= 4:
            return path[:4]
        return path[:3]
    return path[:2]


def assert_resolved_secret_values_stay_at_reference_sites(
    resolved: Mapping[str, Any],
    *,
    compose_path: str | Path,
    environment: Mapping[str, str],
) -> None:
    """resolved 문서에서 `.env` 비밀 값은 원본이 보호 변수를 참조하는 자리에만 나타난다.

    raw 규칙의 백스톱이다. raw 파서가 compose와 다르게 읽거나(이름 글자), 보간 시점에 파일 내용이 들어오면
    (`label_file`, `format: raw`를 뗀 `env_file`) raw 문서만 보는 규칙은 그것을 못 본다. 원본에 `env_file`이 있는
    서비스의 env key는 파일이 채우므로 예외다. compose의 resolved 출력은 `$`를 `$$`로 쓴다.
    """

    reference_text, reference = _load_reference(compose_path)
    secret_set = secret_values(environment, reference_text=reference_text)
    if not secret_set:
        return
    references = compose_references(reference)
    allowed = {
        site
        for site, names in references.items()
        if any(_is_protected(name, environment, secret_set) for name in names)
    }
    env_file_services = {
        site[1] for site in references if site[:1] == ("services",) and site[2:3] == ("env_file",)
    }
    needles = {form for secret in secret_set for form in (secret, secret.replace("$", "$$"))}
    for path, scalar in _sited_scalars(resolved):
        if not any(needle in scalar for needle in needles):
            continue
        site = _site_of(path)
        if site in allowed or (
            site[:1] == ("services",) and site[2:3] == ("environment",) and site[1] in env_file_services
        ):
            continue
        raise ComposeCandidateContractError(
            f"resolved compose candidate carries a protected C6c value at {_describe(site)}"
        )


def assert_protected_references_are_derived(
    candidate: Mapping[str, Any],
    *,
    compose_path: str | Path,
    environment: Mapping[str, str],
) -> None:
    """후보의 보호 참조가 원본 compose 참조의 부분집합이고, 비밀 값이 글자 그대로 들어 있지 않은지 본다."""

    reference_text, reference = _load_reference(compose_path)
    secret_set = secret_values(environment, reference_text=reference_text)
    allowed = compose_references(reference)
    for site, names in sorted(compose_references(candidate).items()):
        added = sorted(
            name
            for name in names - allowed.get(site, set())
            if _is_protected(name, environment, secret_set)
        )
        if added:
            raise ComposeCandidateContractError(
                "compose candidate leaks a protected C6c reference: "
                f"{_describe(site)} -> {', '.join(added)}"
            )
    for site, scalar in _sited_scalars(candidate):
        if any(secret in scalar for secret in secret_set):
            raise ComposeCandidateContractError(
                f"compose candidate carries a protected C6c value literally at {_describe(site)}"
            )
