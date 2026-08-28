"""관리자 비밀번호 회전 계약 테스트 (KUM-M10).

여기서 지키려는 것은 둘이다. (1) `.env`에서 **정확히 한 키만** 바뀐다 — 이 함수가
임의 key=value 쓰기로 자라면 그 순간 `.env` 전체가 HTTP로 편집 가능해진다.
(2) 진행 중인 재구축을 무효화할 수 있을 때는 막되, **못 봤다는 것을 안전으로 읽지
않는다** — journal은 root의 0700 디렉터리에 있어 backend가 늘 볼 수 있는 것이 아니다.

실제 미종결 journal을 만들려면 파괴적 재구축을 돌려야 하므로 그 경로는 mock으로
대체한다(저널에 명시).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import admin_password_service as service
from kor_travel_docker_manager.services.admin_password_service import (
    ADMIN_PASSWORD_HASH_ENV,
    AdminPasswordError,
    change_admin_password,
    pinned_rebuild_guard_state,
)
from kor_travel_docker_manager.services.auth_service import hash_password_for_env

CURRENT = "current-password-1234"
NEXT = "brand-new-password-5678"

_REBUILDABLE_ENV = """KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal
KTDM_DEPLOYMENT_LIFECYCLE=rebuildable
PINVI_ENVIRONMENT=production
KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=true
COMPOSE_PROJECT_NAME=kor-travel-docker-manager
"""


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`.env`를 tmp_path에 만든다 — WSL drvfs에서는 0600이 유지되지 않는다."""

    path = tmp_path / ".env"
    path.write_text(
        f"# comment\nKTDM_ADMIN_USERNAME=admin\n{ADMIN_PASSWORD_HASH_ENV}=placeholder\n"
        "OTHER_KEY=keep-me\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    monkeypatch.setenv("KTDM_ADMIN_USERNAME", "admin")
    monkeypatch.setenv(ADMIN_PASSWORD_HASH_ENV, hash_password_for_env(CURRENT))
    monkeypatch.setenv("KTDM_SESSION_SECRET", "test-session-secret-minimum-32-bytes-value")
    # 기본은 "막을 것이 없음" — 가드 자체는 별도 테스트에서 다룬다.
    monkeypatch.setattr(
        service,
        "pinned_rebuild_guard_state",
        lambda **_: {
            "verdict": "no_journal",
            "detail": "",
            "requires_acknowledgement": False,
            "blocking": False,
        },
    )
    return path


# --- .env 재작성 경계 ---------------------------------------------------------


def test_only_the_password_hash_line_changes(env_file: Path) -> None:
    before = env_file.read_text(encoding="utf-8").split("\n")

    change_admin_password(current_password=CURRENT, new_password=NEXT, env_path=env_file)

    after = env_file.read_text(encoding="utf-8").split("\n")
    assert len(before) == len(after)
    differing = [
        index
        for index, (old, new) in enumerate(zip(before, after, strict=True))
        if old != new
    ]
    assert len(differing) == 1
    assert after[differing[0]].startswith(f"{ADMIN_PASSWORD_HASH_ENV}=")
    assert "OTHER_KEY=keep-me" in after
    assert "# comment" in after


def test_the_live_process_accepts_the_new_password_without_a_restart(
    env_file: Path,
) -> None:
    """이 즉시성이 `verify_admin_password`가 매번 environ을 읽는 이유다."""

    from kor_travel_docker_manager.services.auth_service import verify_admin_password

    change_admin_password(current_password=CURRENT, new_password=NEXT, env_path=env_file)

    assert verify_admin_password("admin", NEXT) == "ok"
    assert verify_admin_password("admin", CURRENT) != "ok"
    assert os.environ[ADMIN_PASSWORD_HASH_ENV] in env_file.read_text(encoding="utf-8")


def test_a_duplicate_assignment_is_refused_rather_than_guessed(env_file: Path) -> None:
    """어느 줄이 유효한지 모호하면 고르지 않는다."""

    env_file.write_text(
        f"{ADMIN_PASSWORD_HASH_ENV}=a\n{ADMIN_PASSWORD_HASH_ENV}=b\n", encoding="utf-8"
    )
    env_file.chmod(0o600)

    with pytest.raises(AdminPasswordError) as caught:
        change_admin_password(
            current_password=CURRENT, new_password=NEXT, env_path=env_file
        )
    assert caught.value.code == "ENV_DUPLICATE_ASSIGNMENT"


def test_a_group_writable_env_is_refused(env_file: Path) -> None:
    env_file.chmod(0o660)

    with pytest.raises(AdminPasswordError) as caught:
        change_admin_password(
            current_password=CURRENT, new_password=NEXT, env_path=env_file
        )
    assert caught.value.code == "ENV_MODE_UNSAFE"


def test_the_key_is_appended_when_absent(env_file: Path) -> None:
    env_file.write_text("OTHER_KEY=keep-me\n", encoding="utf-8")
    env_file.chmod(0o600)

    change_admin_password(current_password=CURRENT, new_password=NEXT, env_path=env_file)

    text = env_file.read_text(encoding="utf-8")
    assert "OTHER_KEY=keep-me" in text
    assert f"{ADMIN_PASSWORD_HASH_ENV}=pbkdf2_sha256:" in text


# --- 자격증명·정책 ------------------------------------------------------------


def test_a_wrong_current_password_is_rejected_before_anything_is_written(
    env_file: Path,
) -> None:
    before = env_file.read_bytes()

    with pytest.raises(AdminPasswordError) as caught:
        change_admin_password(
            current_password="wrong-password", new_password=NEXT, env_path=env_file
        )

    assert caught.value.code == "INVALID_CREDENTIALS"
    assert caught.value.status_code == 401
    assert env_file.read_bytes() == before


@pytest.mark.parametrize(
    ("new_password", "code"),
    [
        ("short", "NEW_PASSWORD_TOO_SHORT"),
        ("has-a-newline\nin-it-1234", "NEW_PASSWORD_INVALID"),
        (CURRENT, "NEW_PASSWORD_UNCHANGED"),
    ],
)
def test_new_password_policy(env_file: Path, new_password: str, code: str) -> None:
    before = env_file.read_bytes()

    with pytest.raises(AdminPasswordError) as caught:
        change_admin_password(
            current_password=CURRENT, new_password=new_password, env_path=env_file
        )

    assert caught.value.code == code
    assert env_file.read_bytes() == before


# --- 미종결 rebuild journal 가드 ---------------------------------------------


def _guard(verdict: str) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "detail": "테스트",
        "requires_acknowledgement": verdict in {"unverifiable", "unknown"},
        "blocking": verdict == "unfinished_journal",
    }


def test_a_proven_unfinished_journal_has_no_override(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """증명됐다는 것은 재개가 실제로 걸려 있다는 뜻이다 — 승인으로도 뚫리지 않는다."""

    monkeypatch.setattr(
        service, "pinned_rebuild_guard_state", lambda **_: _guard("unfinished_journal")
    )
    before = env_file.read_bytes()

    with pytest.raises(AdminPasswordError) as caught:
        change_admin_password(
            current_password=CURRENT,
            new_password=NEXT,
            acknowledge_pinned_rebuild_invalidation=True,
            env_path=env_file,
        )

    assert caught.value.code == "PINNED_REBUILD_JOURNAL_UNFINISHED"
    assert env_file.read_bytes() == before


@pytest.mark.parametrize("verdict", ["unverifiable", "unknown"])
def test_an_unverifiable_guard_requires_an_explicit_acknowledgement(
    env_file: Path, monkeypatch: pytest.MonkeyPatch, verdict: str
) -> None:
    """'못 봤다'는 '안전'이 아니다."""

    monkeypatch.setattr(service, "pinned_rebuild_guard_state", lambda **_: _guard(verdict))

    with pytest.raises(AdminPasswordError) as caught:
        change_admin_password(
            current_password=CURRENT, new_password=NEXT, env_path=env_file
        )
    assert caught.value.code == "PINNED_REBUILD_JOURNAL_UNVERIFIABLE"

    result = change_admin_password(
        current_password=CURRENT,
        new_password=NEXT,
        acknowledge_pinned_rebuild_invalidation=True,
        env_path=env_file,
    )
    assert result["guard"] == verdict
    assert result["acknowledged"] is True


def test_a_non_rebuildable_mode_needs_no_acknowledgement(tmp_path: Path) -> None:
    """이 모드에서는 journal이 만들어지지도 재개되지도 않는다."""

    path = tmp_path / ".env"
    path.write_text(
        "KTDM_DEPLOYMENT_ENVIRONMENT=local\nKTDM_DEPLOYMENT_LIFECYCLE=development\n"
        "PINVI_ENVIRONMENT=development\n"
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED=false\n",
        encoding="utf-8",
    )

    state = pinned_rebuild_guard_state(env_path=path)

    assert state["verdict"] == "not_rebuildable"
    assert state["requires_acknowledgement"] is False
    assert state["blocking"] is False


def test_an_unreadable_state_root_is_unverifiable_not_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(_REBUILDABLE_ENV, encoding="utf-8")
    absent = tmp_path / "state"
    absent.mkdir()
    absent.chmod(0o755)  # 0700이 아니다 → 우리 것이라고 단정할 수 없다
    monkeypatch.setattr(service, "pinned_runtime_state_root", lambda values: absent)

    state = pinned_rebuild_guard_state(env_path=path)

    assert state["verdict"] == "unverifiable"
    assert state["requires_acknowledgement"] is True


def test_an_absent_state_root_reads_as_no_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(_REBUILDABLE_ENV, encoding="utf-8")
    monkeypatch.setattr(
        service, "pinned_runtime_state_root", lambda values: tmp_path / "absent"
    )

    assert pinned_rebuild_guard_state(env_path=path)["verdict"] == "no_journal"


def test_an_unfinished_journal_is_detected_when_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(_REBUILDABLE_ENV, encoding="utf-8")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    (state_root / "pinned-runtime-rebuild-v8-abc.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service, "pinned_runtime_state_root", lambda values: state_root)

    class _Journal:
        phase = "map_runtime_ready"

    monkeypatch.setattr(service, "read_rebuild_journal", lambda journal_path: _Journal())

    state = pinned_rebuild_guard_state(env_path=path)

    assert state["verdict"] == "unfinished_journal"
    assert state["blocking"] is True
    assert "map_runtime_ready" in state["detail"]


def test_a_committed_journal_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(_REBUILDABLE_ENV, encoding="utf-8")
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    (state_root / "pinned-runtime-rebuild-v8-abc.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service, "pinned_runtime_state_root", lambda values: state_root)

    class _Journal:
        phase = "committed"

    monkeypatch.setattr(service, "read_rebuild_journal", lambda journal_path: _Journal())

    assert pinned_rebuild_guard_state(env_path=path)["verdict"] == "no_journal"
