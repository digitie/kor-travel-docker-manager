"""compose_service.py의 `_resolve_repository_path`에 대한 단위 테스트.

이 함수는 어떤 테스트에서도 직접 호출되지 않고 있었다 —
`test_f1d_compose_contract.py`의
`test_candidate_preflight_rejects_a_build_context_outside_staged_source`는
이 함수의 유일한 호출자인 `_map_source_environment_contract_version` 자체를
`monkeypatch.setattr`로 완전히 대체해 버려서, `_resolve_repository_path`의
경로 결합/실패 판정 로직은 실제로 한 번도 실행되지 않은 채 통과해 왔다.

`_resolve_repository_path`는 git을 전혀 shelling-out하지 않는 순수 경로
연산이므로(git 호출은 별도의 `_run_git_read`/`_run_git_bytes`가 담당), 실제
git 저장소 없이도 tmp_path 기반의 평범한 파일/디렉터리만으로 아래 4개
분기를 전부 검증할 수 있다:

1. 상대 경로 -> compose_directory에 결합 후 성공적으로 resolve
2. 절대 경로 -> compose_directory를 무시하고 그대로 resolve
3. resolve 실패(대상이 존재하지 않음) -> "{label} build context cannot be
   resolved" 로 DeploymentContractError
4. resolve는 되지만 디렉터리가 아님(파일) -> "{label} build context is not
   a directory" 로 DeploymentContractError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.errors import DeploymentContractError


def test_relative_configured_path_is_joined_against_compose_directory(
    tmp_path: Path,
) -> None:
    compose_directory = tmp_path / "workspace"
    target = compose_directory / "vendor" / "repo"
    target.mkdir(parents=True)

    resolved = compose_service_module._resolve_repository_path(
        "vendor/repo",
        compose_directory=compose_directory,
        label="Test",
    )

    assert resolved == target.resolve(strict=True)
    assert resolved.is_dir()


def test_absolute_configured_path_bypasses_compose_directory(
    tmp_path: Path,
) -> None:
    absolute_repo = tmp_path / "absolute_repo"
    absolute_repo.mkdir()
    # compose_directory는 존재하지 않는 경로다 — 절대 경로가 주어졌을 때
    # 실제로 compose_directory와 결합되지 않는다는 것을 증명하기 위함이다.
    unused_compose_directory = tmp_path / "does_not_exist_and_must_stay_unused"

    resolved = compose_service_module._resolve_repository_path(
        str(absolute_repo),
        compose_directory=unused_compose_directory,
        label="Test",
    )

    assert resolved == absolute_repo.resolve(strict=True)
    assert resolved.is_dir()


def test_unresolvable_path_raises_cannot_be_resolved(tmp_path: Path) -> None:
    compose_directory = tmp_path / "workspace"
    compose_directory.mkdir()

    with pytest.raises(DeploymentContractError) as exc_info:
        compose_service_module._resolve_repository_path(
            "missing/repo",
            compose_directory=compose_directory,
            label="Test",
        )

    assert str(exc_info.value) == "Test build context cannot be resolved"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_configured_path_pointing_at_a_file_raises_not_a_directory(
    tmp_path: Path,
) -> None:
    compose_directory = tmp_path / "workspace"
    compose_directory.mkdir()
    not_a_directory = compose_directory / "repo.txt"
    not_a_directory.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(DeploymentContractError) as exc_info:
        compose_service_module._resolve_repository_path(
            "repo.txt",
            compose_directory=compose_directory,
            label="Test",
        )

    assert str(exc_info.value) == "Test build context is not a directory"
