"""백업과 pin registry 보존본의 off-box(원격) 동기화 (GM-08).

`standalone_backup.py`의 6개 role 백업과 `runtime_pin_registry.py`의 registry
보존본은 전부 `KTDM_BACKUP_ROOT`/`/var/lib/kor-travel-docker-manager` 같은 로컬
경로에만 있다 — 호스트 디스크가 죽으면 DB 백업과 pin rollback의 유일한 소스가
함께 사라진다(ADR-40 트레이드오프가 이미 자인한 공백). 이 모듈은 `rsync`로 설정된
원격 호스트에 그 자료를 옮기고, 옮긴 뒤 원격에서 `sha256sum -c`로 다시 확인한다.

전송 대상이 설정되지 않았으면(`KTDM_OFFBOX_HOST` 미설정) 아무 것도 하지 않고
명확한 오류를 낸다 — 조용히 "성공"으로 보고하면 아무도 활성화하지 않은 채 방치된다.

`.dump` 파일은 이미 있는 `.sha256` sidecar(백업 생성 시점에 만든, `sha256sum -c`
그대로 먹는 형태)를 그대로 신뢰해 원격 검증에 쓴다 — 매 동기화마다 수십 GB 백업을
다시 해시하는 비용을 피한다. sidecar가 없는 작은 파일(manifest, pin registry
JSON)만 이 모듈이 즉석에서 스트리밍 해시한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kor_travel_docker_manager.services.runtime_pin_registry import (
    runtime_pin_registry_path,
    runtime_pin_registry_public_path,
)
from kor_travel_docker_manager.services.standalone_backup import (
    BACKUP_ROLES,
    BackupRole,
    backup_root_for_role,
)

OFFBOX_HOST_ENV = "KTDM_OFFBOX_HOST"
OFFBOX_USER_ENV = "KTDM_OFFBOX_USER"
OFFBOX_PORT_ENV = "KTDM_OFFBOX_PORT"
OFFBOX_SSH_KEY_ENV = "KTDM_OFFBOX_SSH_KEY"
OFFBOX_REMOTE_ROOT_ENV = "KTDM_OFFBOX_REMOTE_ROOT"
_DEFAULT_PORT = "22"
_PIN_REGISTRY_REMOTE_SUBPATH = "pin-registry"
_PIN_REGISTRY_PUBLIC_REMOTE_SUBPATH = "pin-registry-public"


class OffboxSyncError(RuntimeError):
    """off-box 동기화 중 발생한 fail-close 오류."""


class OffboxSyncNotConfiguredError(OffboxSyncError):
    """`KTDM_OFFBOX_HOST`가 비어 있어 동기화 대상이 없다."""


@dataclass(frozen=True)
class OffboxDestination:
    host: str
    user: str
    port: str
    ssh_key: str | None
    remote_root: str

    def _ssh_option_args(self) -> list[str]:
        args = ["-p", self.port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if self.ssh_key:
            args += ["-i", self.ssh_key]
        return args

    def rsync_shell(self) -> str:
        return "ssh " + " ".join(shlex.quote(arg) for arg in self._ssh_option_args())

    def ssh_argv(self, remote_command: str) -> list[str]:
        return ["ssh", *self._ssh_option_args(), f"{self.user}@{self.host}", remote_command]

    def remote_path(self, subpath: str) -> str:
        return f"{self.remote_root.rstrip('/')}/{subpath}/"


def _offbox_destination() -> OffboxDestination | None:
    host = os.environ.get(OFFBOX_HOST_ENV, "").strip()
    if not host:
        return None
    user = os.environ.get(OFFBOX_USER_ENV, "").strip()
    if not user:
        raise OffboxSyncError(f"{OFFBOX_HOST_ENV} is set but {OFFBOX_USER_ENV} is missing")
    remote_root = os.environ.get(OFFBOX_REMOTE_ROOT_ENV, "").strip()
    if not remote_root:
        raise OffboxSyncError(
            f"{OFFBOX_HOST_ENV} is set but {OFFBOX_REMOTE_ROOT_ENV} is missing"
        )
    port = os.environ.get(OFFBOX_PORT_ENV, "").strip() or _DEFAULT_PORT
    ssh_key = os.environ.get(OFFBOX_SSH_KEY_ENV, "").strip() or None
    return OffboxDestination(
        host=host, user=user, port=port, ssh_key=ssh_key, remote_root=remote_root
    )


def offbox_sync_is_configured() -> bool:
    return _offbox_destination() is not None


@dataclass(frozen=True)
class OffboxSyncTargetResult:
    label: str
    synced: bool
    verified: bool
    detail: str

    def to_json(self) -> dict[str, object]:
        return {
            "label": self.label,
            "synced": self.synced,
            "verified": self.verified,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class OffboxSyncOutcome:
    destination_host: str
    started_at_unix: int
    duration_sec: float
    targets: tuple[OffboxSyncTargetResult, ...]

    @property
    def all_verified(self) -> bool:
        return bool(self.targets) and all(t.synced and t.verified for t in self.targets)

    def to_json(self) -> dict[str, object]:
        return {
            "destination_host": self.destination_host,
            "started_at_unix": self.started_at_unix,
            "duration_sec": self.duration_sec,
            "targets": [t.to_json() for t in self.targets],
            "all_verified": self.all_verified,
        }


def _run(
    argv: list[str], *, timeout: int, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv, capture_output=True, check=False, timeout=timeout, input=input_bytes
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OffboxSyncError(f"command could not run: {argv[0]}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_directory_checksum_manifest(local_dir: Path) -> str:
    lines: list[str] = []
    for path in sorted(local_dir.iterdir()):
        # 점 파일은 건너뛴다 — `.backup.lock`(role lock)과
        # `.<role>-<ts>.dump.copying`(중단된 백업이 남긴 임시 사본)은 백업 산출물이
        # 아니다. `--delete` 없이 rsync하므로, 한 번 옮겨진 잔해는 로컬에서 지워져도
        # 원격에 영원히 남는다 — 애초에 옮기지 않아야 한다.
        if not path.is_file() or path.name.startswith(".") or path.name.endswith(".sha256"):
            continue
        if path.name.endswith(".dump"):
            sidecar = path.with_name(f"{path.name}.sha256")
            if sidecar.is_file():
                lines.append(sidecar.read_text(encoding="ascii").strip())
                continue
        lines.append(f"{_sha256_file(path)}  {path.name}")
    return "\n".join(line for line in lines if line) + ("\n" if lines else "")


def _plain_directory_checksum_manifest(local_dir: Path) -> str:
    lines = [
        f"{_sha256_file(path)}  {path.name}"
        for path in sorted(local_dir.iterdir())
        if path.is_file() and not path.name.startswith(".")
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _sync_target_safely(
    local_dir: Path,
    destination: OffboxDestination,
    *,
    label: str,
    remote_subpath: str,
    manifest_builder: Callable[[Path], str],
    timeout: int,
) -> OffboxSyncTargetResult:
    """한 대상의 처리 전체(체크섬 계산 + 전송 + 검증)를 예외로부터 격리한다.

    `create_standalone_backup`/`gc_standalone_backups`가 잡는 role lock을 이 함수는
    잡지 않는다 — 그 lock을 rsync 전체 구간에 걸어 두면 대용량 role의 off-box
    전송이 그날 밤 cron 백업 생성을 몇 시간 동안 막을 수 있다(GM-07의 리허설과 같은
    교훈). 대신 그 사이 gc가 파일을 지우는 TOCTOU 경합을 여기서 흡수한다 — 이
    함수가 실패해도 다른 대상은 계속 진행되고, 이미 완료된 대상의 결과는 지워지지
    않는다.
    """

    try:
        if not local_dir.is_dir():
            return OffboxSyncTargetResult(
                label, False, False, f"local directory missing: {local_dir}"
            )
        checksum_manifest = manifest_builder(local_dir)
        return _sync_directory(
            local_dir,
            destination,
            label=label,
            remote_subpath=remote_subpath,
            checksum_manifest=checksum_manifest,
            timeout=timeout,
        )
    except OSError as exc:
        return OffboxSyncTargetResult(
            label,
            False,
            False,
            f"local read failed while preparing this target (possibly a concurrent "
            f"backup create/gc): {exc}",
        )
    except OffboxSyncError as exc:
        # `_run`은 timeout·실행 파일 부재 같은 프로세스 레벨 실패를 예외로 낸다(정상
        # 종료·비정상 exit code와 달리 `_sync_directory`가 반환값으로 처리하지 못하는
        # 경로다). 여기서 잡지 않으면 이 한 대상의 문제가 아직 시도하지 않은 나머지
        # 대상(특히 role 루프 뒤에 오는 pin registry — 이 기능이 지키려는 진짜
        # 대상)까지 전부 건너뛰게 만든다.
        return OffboxSyncTargetResult(label, False, False, f"sync command failed: {exc}")


def _sync_directory(
    local_dir: Path,
    destination: OffboxDestination,
    *,
    label: str,
    remote_subpath: str,
    checksum_manifest: str,
    timeout: int,
) -> OffboxSyncTargetResult:
    if not local_dir.is_dir():
        return OffboxSyncTargetResult(label, False, False, f"local directory missing: {local_dir}")
    if not checksum_manifest.strip():
        return OffboxSyncTargetResult(label, False, False, "no files to sync")

    remote_path = destination.remote_path(remote_subpath)
    rsync_result = _run(
        [
            "rsync",
            "-a",
            "--checksum",
            # 점 파일(`.backup.lock`, 중단된 백업이 남긴 `.*.dump.copying`)은 백업
            # 산출물이 아니다 — 체크섬 매니페스트에서도 빠지므로 여기서도 빼야
            # "옮겼지만 검증 목록에는 없는" 파일이 원격에 남지 않는다.
            "--exclude=.*",
            "-e",
            destination.rsync_shell(),
            f"{local_dir}/",
            f"{destination.user}@{destination.host}:{remote_path}",
        ],
        timeout=timeout,
    )
    if rsync_result.returncode != 0:
        return OffboxSyncTargetResult(
            label,
            False,
            False,
            f"rsync failed (exit {rsync_result.returncode}): "
            f"{rsync_result.stderr.decode('utf-8', 'replace')[:2000]}",
        )

    # sidecar 재사용은 **로컬** 해싱 비용만 아낀다 — 원격 sha256sum -c는 대용량
    # dump 전체를 원격 디스크에서 다시 읽어야 하므로, 이 검증에도 rsync와 같은
    # 여유(호출자가 지정한 timeout)를 줘야 한다. 고정된 짧은 값은 이 기능이 지키려는
    # 바로 그 큰 백업에서 가장 먼저 터진다.
    verify_result = _run(
        destination.ssh_argv(f"cd {shlex.quote(remote_path)} && sha256sum -c -"),
        timeout=timeout,
        input_bytes=checksum_manifest.encode("utf-8"),
    )
    verified = verify_result.returncode == 0
    detail = (
        "synced and verified"
        if verified
        else (
            f"rsync succeeded but remote sha256sum -c failed (exit {verify_result.returncode}): "
            f"{verify_result.stdout.decode('utf-8', 'replace')[:1000]}"
            f"{verify_result.stderr.decode('utf-8', 'replace')[:1000]}"
        )
    )
    return OffboxSyncTargetResult(label, True, verified, detail)


def sync_backups_offbox(
    *,
    roles: tuple[BackupRole, ...] = BACKUP_ROLES,
    backup_root: Path | None = None,
    include_pin_registry: bool = True,
    timeout: int = 14_400,
) -> OffboxSyncOutcome:
    """설정된 원격 호스트로 백업과(옵션으로) pin registry 보존본을 옮기고 검증한다.

    role마다 독립적으로 진행한다 — 한 role의 전송/검증 실패가 나머지를 막지 않는다.
    각 role의 상태는 `OffboxSyncOutcome.targets`에 개별 기록되므로 호출자가 부분
    실패를 그대로 볼 수 있다.

    `backup_root`(주면)는 role 하나를 직접 가리키는 `plan_standalone_restore` 류의
    경로가 아니라, `<backup_root>/<role>/` 구조를 갖는 **여러 role의 공통 상위
    디렉터리**다 — `KTDM_BACKUP_ROOT`env 기본값과 같은 레이아웃이다. 이 함수가 한
    번에 여러 role을 다루므로 role별로 다른 하위 경로가 필요하다.
    """

    destination = _offbox_destination()
    if destination is None:
        raise OffboxSyncNotConfiguredError(
            f"{OFFBOX_HOST_ENV} is not set — off-box sync is not configured"
        )

    started = time.monotonic()
    started_at_unix = int(time.time())
    targets: list[OffboxSyncTargetResult] = []

    for role in roles:
        role_backup_root = backup_root / role if backup_root is not None else None
        local_dir = backup_root_for_role(role, backup_root=role_backup_root)
        targets.append(
            _sync_target_safely(
                local_dir,
                destination,
                label=role,
                remote_subpath=role,
                manifest_builder=_backup_directory_checksum_manifest,
                timeout=timeout,
            )
        )

    if include_pin_registry:
        registry_dir = runtime_pin_registry_path().parent
        targets.append(
            _sync_target_safely(
                registry_dir,
                destination,
                label="pin_registry",
                remote_subpath=_PIN_REGISTRY_REMOTE_SUBPATH,
                manifest_builder=_plain_directory_checksum_manifest,
                timeout=300,
            )
        )
        public_dir = runtime_pin_registry_public_path().parent
        if public_dir != registry_dir:
            targets.append(
                _sync_target_safely(
                    public_dir,
                    destination,
                    label="pin_registry_public",
                    remote_subpath=_PIN_REGISTRY_PUBLIC_REMOTE_SUBPATH,
                    manifest_builder=_plain_directory_checksum_manifest,
                    timeout=300,
                )
            )

    outcome = OffboxSyncOutcome(
        destination_host=destination.host,
        started_at_unix=started_at_unix,
        duration_sec=round(time.monotonic() - started, 3),
        targets=tuple(targets),
    )
    _write_offbox_sync_status(outcome, backup_root=backup_root)
    return outcome


def _offbox_status_path(backup_root: Path | None) -> Path:
    if backup_root is not None:
        return backup_root / ".offbox-sync-status.json"
    base = os.environ.get("KTDM_BACKUP_ROOT", "").strip()
    root = Path(base) if base else Path.home() / "backups"
    return root / ".offbox-sync-status.json"


def _write_offbox_sync_status(outcome: OffboxSyncOutcome, *, backup_root: Path | None) -> None:
    """상태 파일은 비밀이 없다(호스트명·타임스탬프·성공 여부뿐) — 대시보드가 읽을 수
    있도록 `0644`로 남긴다. 쓰기 실패는 이 함수 호출자(동기화 자체)의 성패를
    바꾸지 않는다 — 상태 표시 실패 때문에 이미 끝난 전송 결과를 오류로 바꾸면 안 된다.
    """

    path = _offbox_status_path(backup_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(outcome.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except OSError:
        pass


def read_offbox_sync_status(*, backup_root: Path | None = None) -> dict[str, object] | None:
    """마지막 동기화 결과를 읽는다. 한 번도 돌지 않았거나 읽을 수 없으면 `None`."""

    path = _offbox_status_path(backup_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
