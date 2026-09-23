"""Map application 300 후보(api/dagster 이미지 + 스키마 head).

ADR-101 이전에는 Map 이미지가 쓴 sealed receipt를 파싱해 이 값들을 얻었다. 그
receipt를 쓰던 실행파일이 Map에서 삭제됐으므로, Manager가 직접 이미지를 빌드하고
(`docker buildx build`), 빌드 결과를 관측해서(`docker image inspect`, 이미 있는
`ktm-application-schema head` 명령) 이 값을 채운다 — 검증할 외부 산출물이 없다.

argv·postgres 참조 이미지는 Manager 자신의 고정 상수다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


class MapApplicationCandidateError(RuntimeError):
    """이 후보를 빌드·관측하는 동안 나는 fail-close 오류."""


#: Map dagster 이미지의 고정 실행 argv. 빌드마다 바뀌지 않는 Manager 쪽 상수다.
WEBSERVER_ARGV: Final = (
    "/usr/local/bin/dagster-webserver",
    "-m",
    "kortravelmap.dagster.definitions",
    "-h",
    "0.0.0.0",
    "-p",
    "12702",
)
DAEMON_ARGV: Final = (
    "/usr/local/bin/dagster-daemon",
    "run",
    "-m",
    "kortravelmap.dagster.definitions",
)
STORAGE_MIGRATION_ARGV: Final = ("/usr/local/bin/ktm-dagster-storage", "migrate")

#: Map application 300 DB가 쓰는 PostGIS 참조 이미지.
POSTGRES_IMAGE_ID: Final = "postgis/postgis:16-3.5-alpine"


@dataclass(frozen=True)
class MapApplicationCandidate:
    """빌드+관측을 마친 후보 identity. orchestration이 그대로 소비한다."""

    candidate_commit: str
    candidate_git_tree: str
    api_image_id: str
    dagster_image_id: str
    dagster_config_sha256: str
    application_head: str
    postgres_image_id: str = POSTGRES_IMAGE_ID
    webserver_argv: tuple[str, ...] = WEBSERVER_ARGV
    daemon_argv: tuple[str, ...] = DAEMON_ARGV
    storage_migration_argv: tuple[str, ...] = STORAGE_MIGRATION_ARGV
