"""Map source 계약 reader가 materialize된 source 트리의 파일을 읽는다(ADR-51 E-1).

재구축의 `candidate_contract` 단계는 매 실행(수렴 포함) Map compose와 env_file을 본다. 종전에는
`git -C <source root> show/ls-tree/cat-file`로 읽었고, 테스트는 이 함수를 통째로 대역으로
바꿔서 source가 git 저장소가 아닐 때(archive) 모든 재구축이 깨져도 CI는 초록이었다. 여기서는
`.git`이 없는 디렉터리를 실제로 읽힌다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import compose_service as compose_service_module
from kor_travel_docker_manager.services.errors import DeploymentContractError

_REVISION = "a" * 40
_ENV_FILE = "packages/kor-travel-map-api/.env"


def _contract(source_root: Path) -> int:
    return compose_service_module._map_source_environment_contract_version(
        {"KOR_TRAVEL_MAP_REPO_DIR": str(source_root)},
        compose_path=str(source_root / "manager" / "docker-compose.yml"),
        source_revision=_REVISION,
    )


def test_the_compose_manifest_is_read_from_the_source_tree(tmp_path: Path) -> None:
    """git 저장소가 아닌 source root에서 compose를 읽어 내용 검사까지 간다.

    옛 reader는 여기서 "cannot inspect Map build context Git state"로 멈췄다.
    """

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    environment: {}\n  frontend:\n    environment: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(DeploymentContractError, match="outside the supported v3/v4 range"):
        _contract(tmp_path)


def test_a_missing_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DeploymentContractError, match="manifest is missing"):
        _contract(tmp_path)


def test_a_manifest_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "real.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").symlink_to("real.yml")

    with pytest.raises(DeploymentContractError, match="manifest is unreadable"):
        _contract(tmp_path)


def _env_file_payload() -> dict[str, object]:
    return {
        "services": {
            name: {"env_file": entries}
            for name, entries in compose_service_module._MAP_SOURCE_ENV_FILE_CONTRACT.items()
        }
    }


def _validate_env_files(source_root: Path) -> None:
    compose_service_module._validate_map_source_env_files(source_root, _env_file_payload())


def _write_env_file(source_root: Path, content: bytes, *, mode: int = 0o644) -> Path:
    path = source_root / _ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)
    return path


def test_an_untracked_env_file_is_skipped(tmp_path: Path) -> None:
    """핀된 revision이 추적하지 않는 env_file은 source 트리에 없다 — runtime에만 생긴다."""

    _validate_env_files(tmp_path)


def test_a_clean_tracked_env_file_passes(tmp_path: Path) -> None:
    _write_env_file(tmp_path, b"KOR_TRAVEL_MAP_API_LOG_LEVEL=info\n")

    _validate_env_files(tmp_path)


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        ("symlink", "not a regular 100644 blob"),
        ("executable", "not a regular 100644 blob"),
        ("oversized", "exceeds 64 KiB"),
        ("not_utf8", "not UTF-8"),
        ("protected", "contains protected wiring"),
    ],
)
def test_a_tracked_env_file_is_held_to_the_contract(
    tmp_path: Path, variant: str, message: str
) -> None:
    if variant == "symlink":
        (tmp_path / "elsewhere.env").write_text("A=1\n", encoding="utf-8")
        path = tmp_path / _ENV_FILE
        path.parent.mkdir(parents=True)
        path.symlink_to(os.path.relpath(tmp_path / "elsewhere.env", path.parent))
    elif variant == "executable":
        _write_env_file(tmp_path, b"A=1\n", mode=0o755)
    elif variant == "oversized":
        _write_env_file(
            tmp_path, b"A=" + b"x" * compose_service_module._MAP_SOURCE_TRACKED_ENV_FILE_MAX_BYTES
        )
    elif variant == "not_utf8":
        _write_env_file(tmp_path, b"A=\xff\xfe\n")
    else:
        protected = next(iter(compose_service_module._MAP_SOURCE_PROTECTED_ENV_VALUES))
        _write_env_file(tmp_path, f"{protected}=leak\n".encode())

    with pytest.raises(DeploymentContractError, match=message):
        _validate_env_files(tmp_path)
