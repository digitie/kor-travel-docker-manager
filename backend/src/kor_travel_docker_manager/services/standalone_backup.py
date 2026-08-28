"""전용 PostgreSQL 인스턴스별 독립 백업 (issue #177).

ADR-37 4-instance 분리(geo/concierge/map/pinvi) 뒤에도 백업 주체는 map 하나뿐이었다.
이 모듈은 v5 rebuild의 cache-target/compatible-pair 기계와 완전히 무관하게, 네
인스턴스 각각을 `docker exec` + `pg_dump`로 독립 백업한다.

산출물은 `docs/docker-management.md`의 "3종 세트" 관례를 따른다 —
`<role>-<ts>.dump` · `<role>-<ts>.dump.sha256`(`sha256sum -c` 그대로 먹는 형태) ·
`<role>-<ts>.manifest`.

포트·admin role 이름은 하드코딩하지 않고 살아있는 컨테이너에서 읽는다
(`_discover_port`/`_discover_admin_role`) — `.env`가 기본 포트를 덮어썼거나
role 이름이 프로젝트마다 달라도(예: map은 `KOR_TRAVEL_MAP_POSTGRES_USER`에
기본값이 없다) 항상 실제 기동값과 일치한다. host network + 프로젝트별 포트라
`--port`를 빠뜨리면 컨테이너 기본값 5432를 찾아 조용히 실패한다.

connection은 TCP가 아니라 `docker exec --user postgres` + unix socket을 쓴다 —
로컬 소켓 인증은 `trust`로 남아 있어(호스트 TCP만 scram으로 잠갔다) 비밀번호
없이 붙을 수 있고, 그래서 이 모듈은 어떤 postgres 비밀번호도 읽거나 다루지
않는다.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BackupRole = Literal[
    "geo",
    "geo_dagster",
    "concierge",
    "map_application",
    "map_dagster",
    "pinvi",
]

BACKUP_ROLES: tuple[BackupRole, ...] = (
    "geo",
    "geo_dagster",
    "concierge",
    "map_application",
    "map_dagster",
    "pinvi",
)

_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_ROLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
_DATABASE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SCHEMA_REVISION = re.compile(r"^[0-9a-z][0-9a-z_.-]{0,127}$")
_FILENAME = re.compile(r"^[a-z][a-z0-9_]{0,32}-[0-9]{1,20}\.dump$")
_ALEMBIC_SCHEMA_CANDIDATES = ("public", "app")

# (container_env, container_default, database_name). container_default는
# config/docker-targets.yml의 4-instance 계약과 같은 이름이다. docker-compose.yml이
# concierge/map/pinvi 컨테이너 이름을 env override로 허용하므로(geo만 리터럴 고정)
# 같은 override를 여기서도 존중한다 — 안 그러면 override된 스택에서 엉뚱한(또는
# 존재하지 않는) 컨테이너를 겨냥해 fail-close로 조용히 실패한다. 포트는 여기 두지
# 않는다 — 실제 기동 인자에서 읽는다.
_ROLE_CONFIG: dict[BackupRole, tuple[str | None, str, str]] = {
    "geo": (None, "kor-travel-geo-postgres", "kor_travel_geo"),
    "geo_dagster": (None, "kor-travel-geo-postgres", "kor_travel_geo_dagster"),
    "concierge": (
        "KOR_TRAVEL_CONCIERGE_POSTGRES_CONTAINER",
        "kor-travel-concierge-postgres",
        "kor_travel_concierge",
    ),
    "map_application": (
        "KOR_TRAVEL_MAP_POSTGRES_CONTAINER",
        "kor-travel-map-postgres",
        "kor_travel_map",
    ),
    "map_dagster": (
        "KOR_TRAVEL_MAP_POSTGRES_CONTAINER",
        "kor-travel-map-postgres",
        "kor_travel_map_dagster",
    ),
    "pinvi": ("PINVI_POSTGRES_CONTAINER", "pinvi-postgres", "pinvi"),
}


class StandaloneBackupError(RuntimeError):
    """백업 생성/조회/정리 중 발생한 fail-close 오류."""


@dataclass(frozen=True)
class BackupManifest:
    role: BackupRole
    created_at_unix: int
    duration_sec: float
    byte_size: int
    sha256: str
    backup_filename: str
    instance: str
    db_size_bytes: int
    toc_entry_count: int
    alembic_head: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "role": self.role,
            "created_at_unix": self.created_at_unix,
            "duration_sec": self.duration_sec,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "backup_filename": self.backup_filename,
            "instance": self.instance,
            "db_size_bytes": self.db_size_bytes,
            "toc_entry_count": self.toc_entry_count,
            "alembic_head": self.alembic_head,
        }


@dataclass(frozen=True)
class GcOutcome:
    """gc가 실제로 지운 것. 회전과 잔해 수거는 성격이 달라 분리해 알린다.

    ``deleted``는 "최신 keep개만 남긴다"는 정책의 결과이고, ``orphans_removed``는
    중단된 create가 남긴 복원 불가능한 dump다. 둘을 한 목록으로 합치면 운영자가
    "왜 예상보다 많이 지워졌나"를 알 수 없다.
    """

    deleted: tuple[str, ...]
    orphans_removed: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.deleted) + len(self.orphans_removed)

    def to_json(self) -> dict[str, object]:
        return {
            "deleted": list(self.deleted),
            "orphans_removed": list(self.orphans_removed),
        }


def create_standalone_backup(
    role: BackupRole,
    *,
    backup_root: Path | None = None,
    timeout: int = 14_400,
) -> BackupManifest:
    """`role`의 앱 DB를 `pg_dump -Fc`로 컨테이너 안에 뜬 뒤 host로 복사한다.

    geo(33GB급)처럼 큰 인스턴스는 기본 timeout(4시간)으로도 부족할 수 있다 —
    호출자가 `timeout`을 넉넉히 늘려야 한다. **timeout에 걸리면 로컬 `docker exec`
    client만 중단되고 컨테이너 안의 `pg_dump`는 서버 쪽에서 계속 실행된다**(docker
    exec는 timeout을 안쪽 프로세스로 전파하지 않는다) — 같은 role을 바로 재시도하면
    두 pg_dump가 동시에 돌아 DB에 이중 부하가 걸릴 수 있으므로, 같은 role의 동시
    실행은 아래 파일 락으로 막는다.
    """

    container_name, database_name = _role_config(role)
    port = _discover_port(container_name)
    admin_name = _discover_admin_role(container_name)

    root = _resolve_backup_root(role, backup_root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)

    with _role_lock(root):
        created_at_unix = int(time.time())
        filename = f"{role}-{created_at_unix}.dump"
        dest_path = root / filename
        copy_path = root / f".{filename}.copying"
        container_tmp = f"/tmp/{filename}"

        try:
            started = time.monotonic()
            _run_checked(
                [
                    "docker",
                    "exec",
                    "--user",
                    "postgres",
                    container_name,
                    "pg_dump",
                    "--username",
                    admin_name,
                    "--port",
                    str(port),
                    "--dbname",
                    database_name,
                    "--format=custom",
                    "--compress=6",
                    "--file",
                    container_tmp,
                ],
                label=f"{role} pg_dump",
                timeout=timeout,
            )
            duration_sec = round(time.monotonic() - started, 3)
            toc_entry_count = _count_toc_entries(container_name, container_tmp, timeout=timeout)
            copy_path.unlink(missing_ok=True)
            _run_checked(
                ["docker", "cp", f"{container_name}:{container_tmp}", str(copy_path)],
                label=f"{role} backup copy-out",
                timeout=timeout,
            )
            if not copy_path.is_file():
                raise StandaloneBackupError(f"{role} backup copy-out produced no file")
            os.chmod(copy_path, 0o600)
            os.replace(copy_path, dest_path)
        finally:
            # pg_dump 실패/timeout이어도 시도한 만큼은 지운다 — 시도가 계속 서버
            # 쪽에서 돌고 있더라도 rm은 디렉터리 항목을 즉시 없애 다음 목록/GC가
            # 반쪽 파일을 보지 않게 한다(inode는 그 프로세스가 끝나야 실제 회수된다).
            copy_path.unlink(missing_ok=True)
            subprocess.run(
                ["docker", "exec", container_name, "rm", "-f", container_tmp],
                capture_output=True,
                check=False,
                timeout=30,
            )

        return _finish_standalone_backup(
            role=role,
            container_name=container_name,
            database_name=database_name,
            port=port,
            admin_name=admin_name,
            dest_path=dest_path,
            filename=filename,
            root=root,
            created_at_unix=created_at_unix,
            duration_sec=duration_sec,
            toc_entry_count=toc_entry_count,
        )


def _finish_standalone_backup(
    *,
    role: BackupRole,
    container_name: str,
    database_name: str,
    port: int,
    admin_name: str,
    dest_path: Path,
    filename: str,
    root: Path,
    created_at_unix: int,
    duration_sec: float,
    toc_entry_count: int,
) -> BackupManifest:
    if not dest_path.is_file():
        raise StandaloneBackupError(f"{role} backup copy-out produced no file")
    os.chmod(dest_path, 0o600)
    byte_size = dest_path.stat().st_size
    if byte_size == 0:
        dest_path.unlink(missing_ok=True)
        raise StandaloneBackupError(f"{role} backup produced an empty file")

    sha256 = _sha256_file(dest_path)
    # `sha256sum -c`가 그대로 먹는 형태: "<hash>  <filename>"
    sha256_path = root / f"{filename}.sha256"
    _atomic_write_bytes(sha256_path, f"{sha256}  {filename}\n".encode("ascii"))
    os.chmod(sha256_path, 0o600)

    manifest = BackupManifest(
        role=role,
        created_at_unix=created_at_unix,
        duration_sec=duration_sec,
        byte_size=byte_size,
        sha256=sha256,
        backup_filename=filename,
        instance=f"{container_name}:127.0.0.1:{port}/{database_name}",
        db_size_bytes=_query_db_size(container_name, port, admin_name, database_name),
        toc_entry_count=toc_entry_count,
        alembic_head=_discover_alembic_head(container_name, port, admin_name, database_name),
    )
    manifest_path = _manifest_path(root, filename)
    _atomic_write_json(manifest_path, manifest.to_json())
    os.chmod(manifest_path, 0o600)
    return manifest


def list_standalone_backups(
    role: BackupRole,
    *,
    backup_root: Path | None = None,
) -> list[BackupManifest]:
    _role_config(role)
    root = _resolve_backup_root(role, backup_root)
    if not root.is_dir():
        return []
    manifests = [
        _read_manifest(path, expected_role=role) for path in sorted(root.glob("*.manifest"))
    ]
    return sorted(manifests, key=lambda item: item.created_at_unix)


def gc_standalone_backups(
    role: BackupRole,
    *,
    keep: int,
    backup_root: Path | None = None,
) -> GcOutcome:
    """가장 최신 `keep`개만 남기고 나머지 dump/sha256/manifest 세트를 지운다.

    **create와 같은 role lock 아래에서 실행한다.** 락이 없으면 진행 중인 백업
    (geo는 실측 20분 이상)의 산출물을 지울 수 있다 — dump는 manifest보다 먼저
    쓰이므로 그 창에서는 orphan과 구분되지 않는다.
    """

    if keep < 1:
        raise StandaloneBackupError("keep must be at least 1")
    root = _resolve_backup_root(role, backup_root)
    if not root.is_dir():
        return GcOutcome(deleted=(), orphans_removed=())
    with _role_lock(root):
        manifests = list_standalone_backups(role, backup_root=backup_root)
        deleted: list[str] = []
        for manifest in manifests[: max(len(manifests) - keep, 0)]:
            _unlink_backup_set(root, manifest.backup_filename)
            deleted.append(manifest.backup_filename)
        # manifest가 없는 dump는 목록에도 안 잡히고 복원 경로도 없다(무결성 메타가
        # 없어 검증할 수 없다). 중단된 create의 잔해이므로 락 아래에서만 수거한다.
        kept_names = {manifest.backup_filename for manifest in manifests} - set(deleted)
        orphans: list[str] = []
        for dump in sorted(root.glob("*.dump")):
            if dump.name in kept_names or not _FILENAME.fullmatch(dump.name):
                continue
            _unlink_backup_set(root, dump.name)
            orphans.append(dump.name)
    return GcOutcome(deleted=tuple(deleted), orphans_removed=tuple(orphans))


def _unlink_backup_set(root: Path, backup_filename: str) -> None:
    """dump·sha256·manifest 3종 세트를 함께 지운다."""

    if not _FILENAME.fullmatch(backup_filename):
        raise StandaloneBackupError(f"backup filename is invalid: {backup_filename}")
    (root / backup_filename).unlink(missing_ok=True)
    (root / f"{backup_filename}.sha256").unlink(missing_ok=True)
    _manifest_path(root, backup_filename).unlink(missing_ok=True)


def _role_config(role: BackupRole) -> tuple[str, str]:
    if role not in _ROLE_CONFIG:
        raise StandaloneBackupError(f"unknown backup role: {role}")
    container_env, container_default, database_name = _ROLE_CONFIG[role]
    container_name = container_default
    if container_env is not None:
        override = os.environ.get(container_env, "").strip()
        if override:
            container_name = override
    return container_name, database_name


@contextlib.contextmanager
def _role_lock(root: Path) -> Iterator[None]:
    """같은 role의 동시 백업 생성을 막는다 — 겹치면 두 pg_dump가 같은
    `container_tmp`/`dest_path`에 동시에 쓰면서 서로의 산출물을 덮어쓸 수 있다."""

    lock_path = root / ".backup.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StandaloneBackupError(
                "another backup is already running for this role"
            ) from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _resolve_backup_root(role: BackupRole, backup_root: Path | None) -> Path:
    if backup_root is not None:
        return backup_root
    base = os.environ.get("KTDM_BACKUP_ROOT", "").strip()
    root = Path(base) if base else Path.home() / "backups"
    return root / role


def _manifest_path(root: Path, backup_filename: str) -> Path:
    if not backup_filename.endswith(".dump"):
        raise StandaloneBackupError(f"backup filename is invalid: {backup_filename}")
    return root / f"{backup_filename[: -len('.dump')]}.manifest"


def _discover_port(container_name: str) -> int:
    """실행 인자의 `-p <port>`를 읽는다 — host network + 프로젝트별 포트라
    `.env` override 여부와 무관하게 항상 실제 listen 포트와 일치해야 한다."""

    if not _CONTAINER_NAME.fullmatch(container_name):
        raise StandaloneBackupError("container name is invalid")
    output = _run_checked(
        ["docker", "inspect", "--format", "{{json .Config.Cmd}}", container_name],
        label=f"{container_name} command introspection",
        timeout=30,
    )
    try:
        cmd = json.loads(output)
    except json.JSONDecodeError as exc:
        raise StandaloneBackupError(f"{container_name} command introspection is invalid") from exc
    if not isinstance(cmd, list):
        raise StandaloneBackupError(f"{container_name} command introspection is invalid")
    for index, token in enumerate(cmd):
        if token == "-p" and index + 1 < len(cmd):
            candidate = cmd[index + 1]
            if isinstance(candidate, str) and candidate.isdigit():
                return int(candidate)
    raise StandaloneBackupError(f"{container_name} does not declare an explicit -p port")


def _discover_admin_role(container_name: str) -> str:
    """superuser role 이름을 `.env` 변수명 추측 대신 살아있는 `Config.Env`에서 읽는다.

    `POSTGRES_USER`는 role 식별자일 뿐 비밀이 아니다 — `POSTGRES_PASSWORD`는
    절대 읽지 않는다(issue #178 이후 secret file로만 존재해 여기서 볼 수도 없다).
    """

    if not _CONTAINER_NAME.fullmatch(container_name):
        raise StandaloneBackupError("container name is invalid")
    output = _run_checked(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
            container_name,
        ],
        label=f"{container_name} environment introspection",
        timeout=30,
    ).decode("utf-8", "replace")
    values = [
        line[len("POSTGRES_USER=") :]
        for line in output.splitlines()
        if line.startswith("POSTGRES_USER=")
    ]
    if len(values) != 1 or not _ROLE_NAME.fullmatch(values[0]):
        raise StandaloneBackupError(f"{container_name} POSTGRES_USER is missing or invalid")
    return values[0]


def _count_toc_entries(container_name: str, container_dump_path: str, *, timeout: int) -> int:
    """`pg_restore --list`의 TOC 항목 수 — 문서의 수동 baseline 검증과 같은 방식이다.

    dump가 실제로 복원 가능한 형태인지의 값싼 sanity check이기도 하다. 스키마·시퀀스·
    트리거를 포함한 전체 TOC 항목 수이지 테이블 수만이 아니다.
    """

    output = _run_checked(
        ["docker", "exec", container_name, "pg_restore", "--list", container_dump_path],
        label="backup TOC listing",
        timeout=timeout,
    ).decode("utf-8", "replace")
    return sum(
        1 for line in output.splitlines() if line.strip() and not line.lstrip().startswith(";")
    )


def _query_db_size(container_name: str, port: int, admin_name: str, database_name: str) -> int:
    if not _DATABASE_IDENTIFIER.fullmatch(database_name):
        raise StandaloneBackupError("database name is invalid")
    output = _run_checked(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            container_name,
            "psql",
            "--username",
            admin_name,
            "--port",
            str(port),
            "--dbname",
            "postgres",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--command",
            f"SELECT pg_database_size('{database_name}')",
        ],
        label=f"{database_name} size query",
        timeout=30,
    ).decode("ascii", "replace").strip()
    if not output.isdigit():
        raise StandaloneBackupError(f"{database_name} size query returned an unexpected value")
    return int(output)


def _discover_alembic_head(
    container_name: str, port: int, admin_name: str, database_name: str
) -> str | None:
    """alembic head를 best-effort로 읽는다 — 프로젝트마다 schema 위치가 달라
    (map/geo/concierge는 `public`, pinvi는 `app`) 실패해도 백업 자체는 막지 않는다."""

    for schema in _ALEMBIC_SCHEMA_CANDIDATES:
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "postgres",
                container_name,
                "psql",
                "--username",
                admin_name,
                "--port",
                str(port),
                "--dbname",
                database_name,
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--command",
                f'SELECT version_num FROM "{schema}"."alembic_version"',
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0 or completed.stderr:
            continue
        lines = completed.stdout.decode("utf-8", "replace").strip().splitlines()
        if len(lines) == 1 and _SCHEMA_REVISION.fullmatch(lines[0]):
            return lines[0]
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    _atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")


def _read_manifest(
    manifest_path: Path,
    *,
    expected_role: BackupRole | None = None,
) -> BackupManifest:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StandaloneBackupError(f"manifest is unreadable: {manifest_path.name}") from exc
    try:
        role = data["role"]
        created_at_unix = int(data["created_at_unix"])
        duration_sec = float(data["duration_sec"])
        byte_size = int(data["byte_size"])
        sha256 = str(data["sha256"])
        backup_filename = str(data["backup_filename"])
        instance = str(data["instance"])
        db_size_bytes = int(data["db_size_bytes"])
        toc_entry_count = int(data["toc_entry_count"])
        alembic_head = data["alembic_head"]
        if alembic_head is not None:
            alembic_head = str(alembic_head)
    except (KeyError, TypeError, ValueError) as exc:
        raise StandaloneBackupError(f"manifest is malformed: {manifest_path.name}") from exc
    if role not in _ROLE_CONFIG:
        raise StandaloneBackupError(f"manifest role is invalid: {manifest_path.name}")
    if not _FILENAME.fullmatch(backup_filename):
        raise StandaloneBackupError(f"manifest backup_filename is invalid: {manifest_path.name}")
    # manifest 내용이 **자기 파일 이름과 결박**되지 않으면, 손상되거나 손으로 편집된
    # manifest 하나가 gc로 하여금 전혀 다른(살아 있는) 백업을 지우게 만든다.
    # 정본은 파일 이름이므로 내용이 그와 다르면 그 manifest를 신뢰하지 않는다.
    if _manifest_path(manifest_path.parent, backup_filename).name != manifest_path.name:
        raise StandaloneBackupError(
            f"manifest backup_filename does not match its own file: {manifest_path.name}"
        )
    if expected_role is not None and role != expected_role:
        raise StandaloneBackupError(
            f"manifest role does not match the requested role: {manifest_path.name}"
        )
    return BackupManifest(
        role=role,
        created_at_unix=created_at_unix,
        duration_sec=duration_sec,
        byte_size=byte_size,
        sha256=sha256,
        backup_filename=backup_filename,
        instance=instance,
        db_size_bytes=db_size_bytes,
        toc_entry_count=toc_entry_count,
        alembic_head=alembic_head,
    )


def _run_checked(arguments: list[str], *, label: str, timeout: int) -> bytes:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StandaloneBackupError(f"{label} could not run") from exc
    if completed.returncode != 0 or completed.stderr:
        raise StandaloneBackupError(
            f"{label} failed (exit {completed.returncode}): "
            f"{completed.stderr.decode('utf-8', 'replace')[:2000]}"
        )
    if not isinstance(completed.stdout, bytes):
        raise StandaloneBackupError(f"{label} produced invalid output")
    return completed.stdout
