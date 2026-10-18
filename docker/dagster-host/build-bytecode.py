# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Youn-sok Choi (digitie)
"""이미지 빌드에서만 명시적으로 stdlib bytecode를 준비한다."""

from __future__ import annotations

import argparse
import compileall
import importlib.util
import os
import py_compile
import re
import sys
import sysconfig
from pathlib import Path

_EXCLUDED = frozenset({"site-packages", "dist-packages", "test", "tests", "idle_test"})


def _walk_error(error: OSError) -> None:
    raise error


def _check_destinations(root: Path) -> None:
    """외부 cache 경로와 조용히 생략될 읽기 실패를 컴파일 전에 거절한다."""
    if sys.pycache_prefix is not None:
        raise ValueError("별도 pycache prefix는 지원하지 않습니다.")
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        parent = Path(directory)
        dirs[:] = [
            name
            for name in dirs
            if name not in _EXCLUDED
            and name != "__pycache__"
            and not (parent / name).is_symlink()
        ]
        for name in files:
            source = parent / name
            if not name.endswith(".py") or not source.is_file():
                continue
            # compileall의 limit_sl_dest도 같은 외부 source 링크를 생략한다.
            if not source.resolve(strict=True).is_relative_to(root):
                continue
            target = Path(importlib.util.cache_from_source(str(source), optimization=""))
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise ValueError("bytecode 목적지가 stdlib 범위를 벗어납니다.")
            count += 1
    if not count:
        raise ValueError("컴파일할 stdlib 소스가 없습니다.")


def _compile_stdlib() -> bool:
    root = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("stdlib 경로가 디렉터리가 아닙니다.")
    _check_destinations(root)
    return compileall.compile_dir(
        str(root),
        workers=1,
        force=True,
        quiet=2,
        optimize=0,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        rx=re.compile(
            r"^" + re.escape(str(root))
            + r"[\\/](?:.*[\\/])?(?:site-packages|dist-packages|test|tests|idle_test)(?:[\\/]|$)"
        ),
        limit_sl_dest=str(root),
    )


def main(argv: list[str] | None = None) -> int:
    """임의 경로 없이 현재 interpreter의 stdlib만 명시적으로 컴파일한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdlib", action="store_true", required=True, help="현재 interpreter의 stdlib만 준비"
    )
    parser.parse_args(argv)
    try:
        success = _compile_stdlib()
    except (OSError, ValueError):
        success = False
    if not success:
        print("표준 라이브러리 bytecode 준비에 실패했습니다.", file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
