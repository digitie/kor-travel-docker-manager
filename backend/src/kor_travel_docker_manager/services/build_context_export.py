"""Compose build가 운영 source 디렉터리를 context로 읽지 않게 하는 정규화 사본.

운영 ``*_REPO_DIR``은 owner-only(700/600)로 둘 수 있고, git 메타데이터가 없는 동기화 사본이기도
하다. 그 디렉터리를 그대로 build context로 쓰면 COPY가 mode를 이미지에 옮겨서 비-root
``USER``로 도는 프로세스가 자기 코드를 읽지 못한다(2026-10-27 Concierge code server
``No module named 'ktc.dagster'``). 그래서 Manager가 내보내는 Compose 문서에서 build context는
둘 중 하나다.

* 이 명령이 **명시적으로 빌드하는** 서비스(``build`` 명령, ``--build``): context를 Manager state
  아래 임시 디렉터리의 사본으로 바꾼다. 사본에는 Docker가 보낼 것(그 context의 ignore 파일이
  거르지 않은 것)만 담고, 디렉터리는 ``0755``, 파일은 ``0644``(owner 실행 비트가 있으면
  ``0755``)로 둔다 — ``git archive``(``tar.umask=0022``)가 만드는 mode와 같다.
* 그 밖의 서비스: ``build`` 절을 지운다. 이미지가 없으면 Compose는 운영 디렉터리에서 조용히
  빌드하는 대신 실패한다(implicit build 금지). Compose의 config hash는 ``build``를 보지 않으므로
  재생성 판단은 바뀌지 않는다.

ignore 판정은 Docker(moby/patternmatcher)의 규칙을 옮겼다. 사본에 ignore 파일과 Dockerfile을
항상 남기므로 Docker가 사본에 같은 규칙을 다시 적용한다 — 이 판정이 Docker보다 덜 거르는 쪽으로
어긋나도 이미지 내용은 같고, 더 거르는 쪽은 아래 테스트가 막는다.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import stat
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

_DIRECTORY_MODE = 0o755
_FILE_MODE = 0o644
_EXECUTABLE_FILE_MODE = 0o755
_REMOTE_CONTEXT = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@)", re.IGNORECASE)
_REGEX_ESCAPED = frozenset(".+()|{}$")


@dataclass(frozen=True)
class _IgnorePattern:
    regex: re.Pattern[str]
    dirs: int
    exception: bool


class DockerIgnore:
    """``.dockerignore`` 판정(moby ``dockerignore.ReadAll`` + ``patternmatcher``)."""

    def __init__(self, lines: Iterable[str]) -> None:
        patterns: list[_IgnorePattern] = []
        for index, raw in enumerate(lines):
            line = raw.lstrip("﻿") if index == 0 else raw
            pattern = line.strip()
            if not pattern or pattern.startswith("#"):
                continue
            exception = pattern.startswith("!")
            if exception:
                pattern = pattern[1:].strip()
            if pattern:
                pattern = posixpath.normpath(pattern)
                while pattern.startswith("//"):
                    pattern = pattern[1:]
                if len(pattern) > 1 and pattern.startswith("/"):
                    pattern = pattern[1:]
            if not pattern:
                raise DeploymentContractError("build context ignore file has an empty exception")
            try:
                regex = re.compile(_pattern_regex(pattern))
            except re.error as exc:
                raise DeploymentContractError("build context ignore pattern is invalid") from exc
            patterns.append(
                _IgnorePattern(regex=regex, dirs=len(pattern.split("/")), exception=exception)
            )
        self._patterns = tuple(patterns)
        self.has_exceptions = any(pattern.exception for pattern in patterns)

    @classmethod
    def from_file(cls, path: Path | None) -> DockerIgnore:
        if path is None:
            return cls(())
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(())
        except (OSError, UnicodeError) as exc:
            raise DeploymentContractError("build context ignore file cannot be read") from exc
        return cls(text.splitlines())

    def excludes(self, relative: str) -> bool:
        """``MatchesOrParentMatches``: 마지막으로 맞은 규칙이 이긴다. 부모 경로가 맞아도 맞는다."""

        parent = posixpath.dirname(relative)
        parent_parts = parent.split("/") if parent else []
        matched = False
        for pattern in self._patterns:
            if pattern.exception != matched:
                continue
            match = pattern.regex.match(relative) is not None
            if not match and parent_parts:
                for end in range(1, len(parent_parts) + 1):
                    if pattern.regex.match("/".join(parent_parts[:end])) is not None:
                        match = True
                        break
            if match:
                matched = not pattern.exception
        return matched


def _pattern_regex(pattern: str) -> str:
    result = ["^"]
    index = 0
    length = len(pattern)
    while index < length:
        character = pattern[index]
        index += 1
        if character == "*":
            if index < length and pattern[index] == "*":
                index += 1
                if index < length and pattern[index] == "/":
                    index += 1
                result.append(".*" if index >= length else "(.*/)?")
            else:
                result.append("[^/]*")
        elif character == "?":
            result.append("[^/]")
        elif character in _REGEX_ESCAPED:
            result.append("\\" + character)
        elif character == "\\":
            if index < length:
                result.append("\\" + pattern[index])
                index += 1
            else:
                result.append("\\\\")
        else:
            result.append(character)
    result.append("$")
    return "".join(result)


def export_build_context(
    context: Path,
    destination: Path,
    *,
    ignore: DockerIgnore,
    keep: Iterable[str] = (),
) -> None:
    """``context``에서 Docker가 보낼 항목만 mode를 정규화해 ``destination``(아직 없음)에 복사한다.

    ``keep``(context 상대 경로)은 ignore 규칙과 무관하게 남긴다 — Dockerfile과 ignore 파일이다.
    Docker는 그것들을 제외 목록에 있어도 읽는다. 일반 파일·디렉터리·symlink(가리키는 곳은
    따라가지 않는다) 외의 특수 파일은 build 입력이 아니므로 옮기지 않는다.
    """

    keep_paths = frozenset(keep)
    keep_directories = frozenset(
        "/".join(path.split("/")[:end])
        for path in keep_paths
        for end in range(1, len(path.split("/")))
    )
    destination.mkdir()
    pending = [""]
    try:
        while pending:
            relative_directory = pending.pop()
            source_directory = context / relative_directory if relative_directory else context
            with os.scandir(source_directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
            for entry in entries:
                relative = f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                excluded = relative not in keep_paths and ignore.excludes(relative)
                metadata = entry.stat(follow_symlinks=False)
                target = destination / relative
                if stat.S_ISDIR(metadata.st_mode):
                    if not excluded:
                        target.mkdir(parents=True, exist_ok=True)
                    if not excluded or ignore.has_exceptions or relative in keep_directories:
                        pending.append(relative)
                    continue
                if excluded:
                    continue
                if stat.S_ISREG(metadata.st_mode):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(entry.path, target, follow_symlinks=False)
                    os.chmod(
                        target,
                        _EXECUTABLE_FILE_MODE if metadata.st_mode & stat.S_IXUSR else _FILE_MODE,
                    )
                elif stat.S_ISLNK(metadata.st_mode):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(os.readlink(entry.path), target)
        # mkdir는 호출자의 umask(M05는 077)를 따른다 — 디렉터리 mode를 마지막에 고정한다.
        os.chmod(destination, _DIRECTORY_MODE)
        for current, directories, _files in os.walk(destination):
            for name in directories:
                path = os.path.join(current, name)
                # os.walk는 디렉터리를 가리키는 symlink도 여기 넣는다 — chmod는 그것을 따라가므로
                # (사본 밖을 가리킬 수 있다) 건너뛴다.
                if not os.path.islink(path):
                    os.chmod(path, _DIRECTORY_MODE)
    except OSError as exc:
        raise DeploymentContractError(
            f"build context cannot be exported: {exc.strerror or type(exc).__name__}"
        ) from exc


def compose_document_with_exported_build_contexts(
    document: Mapping[str, Any],
    *,
    build_services: Iterable[str],
    staging_directory: Path | None,
) -> dict[str, Any]:
    """``build_services``의 local context를 정규화 사본으로 바꾸고, 나머지 ``build`` 절을 지운다.

    같은 context·ignore 파일을 쓰는 서비스는 사본 하나를 나눠 쓴다. 사본은
    ``staging_directory`` 아래에 만들고, 그 수명은 호출자가 소유한다.
    """

    rewritten = deepcopy(dict(document))
    services = rewritten.get("services")
    if not isinstance(services, dict):
        return rewritten
    targets = frozenset(build_services)
    # 같은 context·ignore 파일이면 사본 하나 — 그 사본은 그것을 쓰는 모든 Dockerfile을 남긴다.
    plans: list[tuple[dict[str, Any], tuple[Path, Path], Path | None]] = []
    keeps: dict[tuple[Path, Path], set[str]] = {}
    for name, service in services.items():
        if not isinstance(service, dict) or "build" not in service:
            continue
        if name not in targets:
            del service["build"]
            continue
        build = service["build"]
        if not isinstance(build, dict):
            raise DeploymentContractError(f"compose service {name} build is unreadable")
        context_value = build.get("context")
        if isinstance(context_value, str) and _REMOTE_CONTEXT.match(context_value):
            continue
        if build.get("additional_contexts"):
            raise DeploymentContractError(
                f"compose service {name} additional build contexts are unsupported"
            )
        if not isinstance(context_value, str) or not Path(context_value).is_absolute():
            raise DeploymentContractError(f"compose service {name} build context must be absolute")
        try:
            context = Path(context_value).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DeploymentContractError(
                f"compose service {name} build context cannot be resolved"
            ) from exc
        if not context.is_dir():
            raise DeploymentContractError(f"compose service {name} build context is not a directory")
        dockerfile: Path | None = None
        if not build.get("dockerfile_inline"):
            dockerfile_value = build.get("dockerfile") or "Dockerfile"
            if not isinstance(dockerfile_value, str):
                raise DeploymentContractError(f"compose service {name} dockerfile is unreadable")
            dockerfile = Path(os.path.normpath(context / dockerfile_value))
        specific_ignore = Path(f"{dockerfile}.dockerignore") if dockerfile is not None else None
        ignore_file = (
            specific_ignore
            if specific_ignore is not None and specific_ignore.is_file()
            else context / ".dockerignore"
        )
        key = (context, ignore_file)
        keeps.setdefault(key, set()).update(
            path.relative_to(context).as_posix()
            for path in (dockerfile, specific_ignore, context / ".dockerignore")
            if path is not None and path.is_relative_to(context) and path != context
        )
        plans.append((build, key, dockerfile))
    if not plans:
        return rewritten
    if staging_directory is None:
        raise DeploymentContractError("build context export has no staging directory")
    exports: dict[tuple[Path, Path], Path] = {}
    for build, key, dockerfile in plans:
        exported = exports.get(key)
        if exported is None:
            exported = staging_directory / f"context-{len(exports)}"
            export_build_context(
                key[0],
                exported,
                ignore=DockerIgnore.from_file(key[1]),
                keep=keeps[key],
            )
            exports[key] = exported
        build["context"] = str(exported)
        if dockerfile is not None:
            build["dockerfile"] = (
                dockerfile.relative_to(key[0]).as_posix()
                if dockerfile.is_relative_to(key[0])
                else str(dockerfile)
            )
    return rewritten
