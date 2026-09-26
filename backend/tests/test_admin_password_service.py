"""관리자 비밀번호 회전 계약 테스트 (KUM-M10).

여기서 지키려는 것은 `.env`에서 **정확히 한 키만** 바뀐다는 것이다 — 이 함수가
임의 key=value 쓰기로 자라면 그 순간 `.env` 전체가 HTTP로 편집 가능해진다.
(재구축 journal 가드는 ADR-51 B3에서 지웠다 — 배포가 재개하지 않으므로 막을 것이 없다.)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services.admin_password_service import (
    ADMIN_PASSWORD_HASH_ENV,
    AdminPasswordError,
    change_admin_password,
)
from kor_travel_docker_manager.services.auth_service import hash_password_for_env

CURRENT = "current-password-1234"
NEXT = "brand-new-password-5678"


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
    # 이 `.env`는 모드를 적지 않아 재작성이 host 변경 lock G를 잡는다(ADR-51 C-3: 미지정은
    # G, conftest가 tmp로 옮겨 둔다). local 사례는 실행 사용자 `$HOME` 아래 개발 lock을
    # 잡으므로, 실행 호스트의 진짜 home에 lock 디렉터리를 만들지 않게 옮긴다.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KTDM_ADMIN_USERNAME", "admin")
    monkeypatch.setenv(ADMIN_PASSWORD_HASH_ENV, hash_password_for_env(CURRENT))
    monkeypatch.setenv("KTDM_SESSION_SECRET", "test-session-secret-minimum-32-bytes-value")
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


# --- Manager mutation lock (ADR-51 C-2) ----------------------------------------


def _home_dev_lock(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "home"
        / ".local"
        / "state"
        / "kor-travel-docker-manager"
        / "global-mutation.lock"
    )


def test_a_local_env_rewrite_takes_the_per_user_dev_lock(
    env_file: Path, tmp_path: Path
) -> None:
    """local은 비root 개발용 `$HOME` lock이다 — host lock G가 아니다."""

    env_file.write_text(
        env_file.read_text(encoding="utf-8") + "KTDM_DEPLOYMENT_ENVIRONMENT=local\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    change_admin_password(current_password=CURRENT, new_password=NEXT, env_path=env_file)

    assert _home_dev_lock(tmp_path).is_file()
    assert not c6c_deployment._C6C_GLOBAL_MUTATION_LOCK.exists()


@pytest.mark.parametrize(
    "mode_line",
    [
        "KTDM_DEPLOYMENT_ENVIRONMENT=rehearsal\n",
        # 모드 미지정도 G다(ADR-51 C-3, fail closed) — 종전에는 `$HOME` lock이었다.
        "",
    ],
)
def test_a_non_local_env_rewrite_takes_the_host_mutation_lock(
    env_file: Path,
    tmp_path: Path,
    mode_line: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lock은 프로세스 환경이 아니라 **다시 쓸 그 `.env`의 값**에서 정해진다."""

    # 프로세스 환경의 local은 파일에 모드가 없어도 채워 넣지 않는다.
    monkeypatch.setenv("KTDM_DEPLOYMENT_ENVIRONMENT", "local")
    env_file.write_text(
        env_file.read_text(encoding="utf-8") + mode_line,
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    change_admin_password(current_password=CURRENT, new_password=NEXT, env_path=env_file)

    assert c6c_deployment._C6C_GLOBAL_MUTATION_LOCK.is_file()
    assert not _home_dev_lock(tmp_path).exists()
    assert f"{ADMIN_PASSWORD_HASH_ENV}=pbkdf2_sha256:" in env_file.read_text(encoding="utf-8")


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
