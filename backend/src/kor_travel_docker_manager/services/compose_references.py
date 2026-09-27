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

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from kor_travel_docker_manager.services.errors import ComposeCandidateContractError
from kor_travel_docker_manager.services.secret_scrub import is_sensitive_key
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

RELEASE_COMPOSE_NAME = ".ktdm-release-compose.yml"
_RELEASE_MARKER = ".ktdm-source-revision"
_MINIMUM_SECRET_LENGTH = 4

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
        while end < len(text) and (text[end] == "_" or text[end].isalnum()):
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
    return any(secret in value for secret in secret_set)


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
