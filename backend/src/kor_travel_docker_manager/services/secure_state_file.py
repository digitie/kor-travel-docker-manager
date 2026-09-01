"""root 소유 상태 파일의 atomic write·디렉터리 fsync 프리미티브 (GM-10).

mkstemp 기반 atomic write와 디렉터리 fsync가 이 저장소에 여러 벌 따로 구현돼
있었고, 이미 서로 어긋나 있었다: `runtime_pin_registry.py`/
`runtime_pair_rotation.py`는 교체 뒤 디렉터리를 fsync하지만
`runtime_execution_registry.py`의 옛 구현은 그 마지막 단계가 없어 crash 시
`os.replace`의 디렉터리 항목 갱신이 디스크에 반영되지 않고 유실될 수 있었다
(파일 내용 자체의 fsync와는 별개 문제 — POSIX에서 rename의 durability는 디렉터리
자체의 fsync로만 보장된다).

이 모듈은 그 "이미 맞던 구현"을 정본으로 승격한 것이지 새 설계가 아니다.
기존 호출부의 동작을 바꾸지 않는다 — 각자 인라인으로 하던 것을 여기서 한 번만
하게 만들 뿐이다.

범위를 의도적으로 좁혔다: `pinned_runtime_generation.py`의
`_write_public_json`(dir_fd 상대 O_EXCL|O_NOFOLLOW + directory fd fsync)은 여기
`atomic_write_json`보다 더 강한 보장을 가지므로 치환 대상에서 제외한다 — 억지로
맞추면 그쪽을 하향 평준화하게 된다. admin_password_service·map_application_300·
compose_service·pinvi_database_role_credentials·legacy_override_retirement·
standalone_backup·pinvi_bootstrap_credential의 나머지 mkstemp 자리도 각자 다른
O_NOFOLLOW/소유권 정책을 갖고 있어(검증 노트가 4가지 혼재를 확인함) 이번 패스에서는
옮기지 않는다 — 파일마다 그 정책이 실제로 무엇을 지키는지 개별 확인 없이 일괄
치환하면 조용한 보안 완화가 될 수 있다.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def fsync_directory(path: Path) -> None:
    """디렉터리 항목 교체(rename)가 살아남게 한다.

    최선의 노력이다 — 실패해도 조용히 넘어간다. directory fsync를 지원하지 않는
    파일시스템도 있고, 이 단계의 실패로 이미 끝난 파일 교체 자체를 실패로
    되돌리면 안 된다(파일은 이미 올바른 내용으로 존재한다 — 이 단계는 그 사실이
    crash에서도 살아남는지에 관한 추가 보장일 뿐이다).
    """

    try:
        directory_fd = os.open(str(path), os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int,
    directory_mode: int | None = None,
    dir_fsync: bool = True,
) -> None:
    """같은 디렉터리의 임시 파일에 쓰고 파일 fsync 뒤 원자 교체, 이어서(옵션)
    디렉터리 fsync한다.

    `directory_mode`를 주면 부모 디렉터리 mode도 맞춘다 — 파일 mode만 맞아도
    부모가 traverse 불가면 읽는 쪽은 lstat조차 못 한다(`runtime_pin_registry.py`가
    이미 이 이유로 쓰던 파라미터).
    """

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if directory_mode is not None:
        try:
            os.chmod(parent, directory_mode)
        except OSError:
            pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    if dir_fsync:
        fsync_directory(parent)


def atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    mode: int,
    directory_mode: int | None = None,
    dir_fsync: bool = True,
) -> None:
    """`atomic_write_bytes`에 JSON 직렬화를 더한 것 — 기존 호출부와 같은 포맷
    (ensure_ascii, indent=2, 끝에 개행 하나)을 유지한다."""

    body = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    atomic_write_bytes(
        path,
        body.encode("utf-8"),
        mode=mode,
        directory_mode=directory_mode,
        dir_fsync=dir_fsync,
    )


def insecure_mode_allowed(env_name: str) -> bool:
    """개발 전용 완화 env를 하나의 규칙으로 파싱한다.

    이전에는 모듈마다 `.strip() == "1"`과 `== "1"`이 섞여 있었다(공백·개행이
    섞인 shell export 값의 취급이 모듈마다 달랐다는 뜻). `.strip()`을 정본으로
    삼는다 — 이 값은 사람이 명시적으로 "1"을 export하는 opt-in 토글이라 앞뒤
    공백을 관대하게 봐도 의미가 달라지지 않는다.
    """

    return os.environ.get(env_name, "").strip() == "1"
