"""host-wide C6c lease를 전용 서비스 계정으로 옮길 수 있게 하는 seam의 회귀 테스트.

두 가지를 동시에 지킨다.

1. ``KTDM_SERVICE_USER`` 미설정(기본)에서는 예전 계약과 **완전히 동일**해야 한다.
   root만 host lease를 준비·소유할 수 있다.
2. 설정된 경우에만 그 계정이 root와 함께 인정된다. 제3의 uid는 어느 모드에서도
   거부된다 — ``/run/lock``이 ``1777``이라 선점이 실제로 가능하기 때문이다.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import c6c_deployment as c6c_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError

requires_non_root = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root로 실행하면 소유자 판별이 항상 통과해 계약을 증명하지 못한다",
)


def _use_lease_parent(monkeypatch: pytest.MonkeyPatch, parent: Path) -> None:
    """``parent``를 production host lease의 부모로 취급하게 한다."""

    monkeypatch.setattr(
        c6c_module, "_C6C_GLOBAL_MUTATION_LOCK", parent / "global-mutation.lock"
    )


# --- configured_service_uid ------------------------------------------------


def test_service_uid_defaults_to_root_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(c6c_module._SERVICE_USER_ENV, raising=False)
    assert c6c_module.configured_service_uid() == 0


def test_service_uid_ignores_whitespace_only_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, "   ")
    assert c6c_module.configured_service_uid() == 0


def test_service_uid_accepts_numeric_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, "4242")
    assert c6c_module.configured_service_uid() == 4242


def test_service_uid_resolves_account_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, "root")
    assert c6c_module.configured_service_uid() == 0


def test_service_uid_rejects_unknown_account_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, "ktdm-no-such-account")
    with pytest.raises(DeploymentContractError, match="is not a user on this host"):
        c6c_module.configured_service_uid()


# --- _prepare_c6c_lock_directory ------------------------------------------


@requires_non_root
def test_default_mode_still_requires_root_when_lease_directory_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """예전에는 euid 검사가 먼저라 항상 계약 오류였다.

    디렉터리가 이미 root 소유로 있으면 진입 실패가 raw ``PermissionError``로 새어
    나갈 수 있는데, 그러면 운영자가 원인을 알 수 없다. 계약 오류를 유지한다.
    """

    parent = tmp_path / "lease"
    parent.mkdir(mode=0o700)
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.delenv(c6c_module._SERVICE_USER_ENV, raising=False)

    with pytest.raises(DeploymentContractError, match="requires root"):
        c6c_module._prepare_c6c_lock_directory(parent)


@requires_non_root
def test_default_mode_still_requires_root_when_lease_directory_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "lease"
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.delenv(c6c_module._SERVICE_USER_ENV, raising=False)

    with pytest.raises(DeploymentContractError, match="requires root"):
        c6c_module._prepare_c6c_lock_directory(parent)
    assert not parent.exists()


@requires_non_root
def test_service_account_accepts_pre_created_lease_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "lease"
    parent.mkdir(mode=0o700)
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid()))

    c6c_module._prepare_c6c_lock_directory(parent)


@requires_non_root
def test_service_account_refuses_to_create_lease_directory_at_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """런타임 생성이 곧 선점 창이다 — 부팅 시 tmpfiles.d가 만들어 둬야 한다."""

    parent = tmp_path / "lease"
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid()))

    with pytest.raises(DeploymentContractError, match="systemd-tmpfiles"):
        c6c_module._prepare_c6c_lock_directory(parent)
    assert not parent.exists()


@requires_non_root
def test_service_account_rejects_lease_directory_owned_by_third_party(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "lease"
    parent.mkdir(mode=0o700)
    _use_lease_parent(monkeypatch, parent)
    # 디렉터리는 현재 uid 소유인데, 인정 대상은 root와 *다른* uid뿐이다.
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid() + 1))
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)

    with pytest.raises(DeploymentContractError, match="lock directory is unsafe"):
        c6c_module._prepare_c6c_lock_directory(parent)


@requires_non_root
def test_service_account_rejects_world_reachable_lease_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "lease"
    parent.mkdir(mode=0o755)
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid()))

    with pytest.raises(DeploymentContractError, match="lock directory is unsafe"):
        c6c_module._prepare_c6c_lock_directory(parent)


@requires_non_root
def test_service_account_rejects_symlinked_lease_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``is_dir()``은 symlink를 따라가지만 검증은 ``lstat``이라 잡아낸다."""

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    parent = tmp_path / "lease"
    parent.symlink_to(real, target_is_directory=True)
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid()))

    with pytest.raises(DeploymentContractError, match="lock directory is unsafe"):
        c6c_module._prepare_c6c_lock_directory(parent)


@requires_non_root
def test_root_created_lease_directory_is_handed_to_the_service_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """root가 먼저 만들면 ``0700 root:root``가 되어 서비스 계정이 영영 못 들어간다.

    tmpfiles.d 설치를 빠뜨린 호스트에서 조용한 잠금이 되지 않도록, root 경로도
    tmpfiles.d가 만들어 두었을 상태로 수렴시켜야 한다.
    """

    parent = tmp_path / "lease"
    _use_lease_parent(monkeypatch, parent)
    service_uid = os.geteuid()
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(service_uid))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    recorded: list[tuple[Path, int, int, bool]] = []
    monkeypatch.setattr(
        os,
        "chown",
        lambda path, uid, gid, *, follow_symlinks=True: recorded.append(
            (Path(path), uid, gid, follow_symlinks)
        ),
    )

    c6c_module._prepare_c6c_lock_directory(parent)

    assert parent.is_dir()
    assert recorded == [(parent, service_uid, -1, False)]


def test_root_created_lease_directory_is_left_alone_in_default_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "lease"
    _use_lease_parent(monkeypatch, parent)
    monkeypatch.delenv(c6c_module._SERVICE_USER_ENV, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        os,
        "chown",
        lambda *_args, **_kwargs: pytest.fail("기본 모드에서는 소유권을 건드리면 안 된다"),
    )
    # 기본 모드의 소유자 계약은 root뿐이므로, 실제 uid가 root가 아닌 환경에서는
    # 생성 이후 검증에서 걸린다. 여기서 보는 것은 chown이 일어나지 않는다는 사실이다.
    try:
        c6c_module._prepare_c6c_lock_directory(parent)
    except DeploymentContractError as exc:  # pragma: no cover - root 실행 시 미발생
        assert "lock directory is unsafe" in str(exc)
    assert parent.is_dir()


def test_non_production_lock_directory_is_created_without_ownership_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """global lease 이외의 lock 디렉터리는 예전처럼 그냥 만든다."""

    _use_lease_parent(monkeypatch, tmp_path / "lease")
    other = tmp_path / "other" / "nested"

    c6c_module._prepare_c6c_lock_directory(other)

    assert other.is_dir()
    assert stat.S_IMODE(other.lstat().st_mode) == 0o700


# --- _validate_c6c_lock_fd -------------------------------------------------


def _open_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    return descriptor


@requires_non_root
def test_production_lock_fd_rejects_non_root_owner_by_default(tmp_path: Path) -> None:
    descriptor = _open_lock(tmp_path / "global-mutation.lock")
    try:
        with pytest.raises(DeploymentContractError, match="lock is unsafe"):
            c6c_module._validate_c6c_lock_fd(descriptor, production=True)
    finally:
        os.close(descriptor)


@requires_non_root
def test_production_lock_fd_accepts_configured_service_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid()))
    descriptor = _open_lock(tmp_path / "global-mutation.lock")
    try:
        c6c_module._validate_c6c_lock_fd(descriptor, production=True)
    finally:
        os.close(descriptor)


@requires_non_root
def test_production_lock_fd_rejects_third_party_owner_in_service_account_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid() + 1))
    descriptor = _open_lock(tmp_path / "global-mutation.lock")
    try:
        with pytest.raises(DeploymentContractError, match="lock is unsafe"):
            c6c_module._validate_c6c_lock_fd(descriptor, production=True)
    finally:
        os.close(descriptor)


def test_non_production_lock_fd_stays_self_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """rebuild lock은 production이 아니라 서비스 계정 설정과 무관하게 자기 소유를 본다."""

    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid() + 1))
    descriptor = _open_lock(tmp_path / "pinned-runtime-rebuild.lock")
    try:
        c6c_module._validate_c6c_lock_fd(descriptor, production=False)
    finally:
        os.close(descriptor)


@requires_non_root
def test_production_lock_fd_rejects_hardlinked_lease(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(c6c_module._SERVICE_USER_ENV, str(os.geteuid()))
    lock_path = tmp_path / "global-mutation.lock"
    descriptor = _open_lock(lock_path)
    os.link(lock_path, tmp_path / "shadow.lock")
    try:
        with pytest.raises(DeploymentContractError, match="lock is unsafe"):
            c6c_module._validate_c6c_lock_fd(descriptor, production=True)
    finally:
        os.close(descriptor)
