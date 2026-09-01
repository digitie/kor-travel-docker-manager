"""테스트 전역 설정.

pinned revision은 이제 registry 파일에서 온다. 테스트 모듈 일부가 import 시점에
``current_pinned_runtime_release()``를 호출하므로, 그 시점의 registry 경로가
개발자 셸의 ``KTDM_RUNTIME_PINS_FILE``에 좌우되면 수집 자체가 환경에 의존한다.
여기서 저장소에 추적된 읽기 전용 seed로 고정해 결정적으로 만든다. 개별 테스트는
필요하면 monkeypatch로 자기 격리 registry를 계속 지정할 수 있다.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import kor_travel_docker_manager.database as _database_module

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED = _REPO_ROOT / "config" / "runtime-pins.seed.json"

# GM-14 리뷰: test_api.py/test_metrics.py가 모듈 레벨에서
# `kor_travel_docker_manager.database.engine`을 테스트용 인메모리 엔진으로
# 바꿔치기한다. conftest.py는 pytest가 어떤 test_*.py보다도 먼저 임포트하므로,
# 여기서 미리 참조를 잡아 두면 이후 어느 파일이 스와핑을 하든(수집 순서와
# 무관하게) "실제 프로덕션 엔진에 원하는 리스너가 걸려 있는가" 같은 검증을
# 안전하게 할 수 있다.
ORIGINAL_METRICS_DB_ENGINE = _database_module.engine


@pytest.fixture(scope="session")
def original_metrics_db_engine():
    return ORIGINAL_METRICS_DB_ENGINE


os.environ["KTDM_RUNTIME_PINS_FILE"] = str(_SEED)
# 개발 체크아웃이 Windows 공유 마운트(WSL drvfs)에 있으면 모든 파일이 0777로 보고돼
# registry 무결성 검사의 mode 항목을 만족할 수 없다. 테스트에서만 그 항목을 완화한다
# (소유자 검사는 그대로 유효하고, root에서는 이 완화 자체가 무효다).
os.environ.setdefault("KTDM_RUNTIME_PINS_ALLOW_INSECURE_MODE", "1")
# 공개 사본은 저장소를 오염시키지 않도록 임시 경로로 보낸다. seed는 읽기 전용이라
# 테스트가 publish를 실행하지 않지만, 기본값이 저장소 안을 가리키게 두지 않는다.
os.environ.setdefault(
    "KTDM_RUNTIME_PINS_PUBLIC_FILE",
    str(Path(tempfile.gettempdir()) / "ktdm-test-runtime-pins.json"),
)
