"""MapApplicationCandidate -- 빌드+관측을 마친 후보 identity를 담는 얇은 dataclass.

ADR-101 이전에는 이 모듈이 sealed receipt를 파싱하는 450줄짜리 검증기였다. receipt를
쓰던 실행파일이 Map에서 삭제됐으므로, 검증할 외부 산출물이 없다 -- Manager가 직접
`docker buildx build`로 이미지를 빌드하고 `docker image inspect`로 관측한다
(`compose_service.py`의 `_build_map_application_300_images`/
`_load_application_300_candidate`). 이 파일은 남은 dataclass 하나만 다룬다.
"""

from __future__ import annotations

from kor_travel_docker_manager.services.map_application_candidate import (
    POSTGRES_IMAGE_ID,
    MapApplicationCandidate,
)

_COMMIT = "1" * 40
_TREE = "2" * 40
_IMAGE_ID = f"sha256:{'3' * 64}"
_DAGSTER_IMAGE_ID = f"sha256:{'4' * 64}"
_CONFIG_SHA256 = "5" * 64


def _candidate() -> MapApplicationCandidate:
    return MapApplicationCandidate(
        candidate_commit=_COMMIT,
        candidate_git_tree=_TREE,
        api_image_id=_IMAGE_ID,
        dagster_image_id=_DAGSTER_IMAGE_ID,
        dagster_config_sha256=_CONFIG_SHA256,
        application_head="400",
    )


def test_candidate_carries_the_build_and_observation_fields() -> None:
    candidate = _candidate()
    assert candidate.candidate_commit == _COMMIT
    assert candidate.candidate_git_tree == _TREE
    assert candidate.api_image_id == _IMAGE_ID
    assert candidate.dagster_image_id == _DAGSTER_IMAGE_ID
    assert candidate.dagster_config_sha256 == _CONFIG_SHA256
    assert candidate.application_head == "400"


def test_candidate_defaults_to_managers_own_fixed_constants() -> None:
    """postgres/argv 값은 Manager 자신의 상수다 -- receipt가 준 적이 없었다."""

    candidate = _candidate()
    assert candidate.postgres_image_id == POSTGRES_IMAGE_ID
    assert candidate.webserver_argv[0] == "/usr/local/bin/dagster-webserver"
    assert candidate.daemon_argv[0] == "/usr/local/bin/dagster-daemon"
    assert candidate.storage_migration_argv == (
        "/usr/local/bin/ktm-dagster-storage",
        "migrate",
    )
