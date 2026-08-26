#!/usr/bin/env python3
"""Debian 관리 Poetry build backend로 Ktdm offline wheelhouse를 원자적으로 발행한다.

이 도구는 network를 사용하지 않는다. 기존 trusted wheelhouse와 Debian의
``python3-poetry-core`` 패키지를 모두 root-owned·non-writable 입력으로 확인한 뒤,
installer가 소비할 새 wheelhouse directory 하나를 atomic publish한다. 기존 destination은
절대 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Final

_DEFAULT_SOURCE_WHEELHOUSE: Final = Path(
    "/opt/kor-travel-docker-manager/.wheelhouse"
)
_DEFAULT_DESTINATION_WHEELHOUSE: Final = Path(
    "/var/lib/kor-travel-docker-manager/wheelhouse"
)
_SYSTEM_DIST_PACKAGES: Final = Path("/usr/lib/python3/dist-packages")
_DEBIAN_PACKAGE: Final = "python3-poetry-core"
_DPKG: Final = "/usr/bin/dpkg"
_DPKG_QUERY: Final = "/usr/bin/dpkg-query"
_DEBIAN_COMMAND_ENV: Final = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
_WHEEL_NAME: Final = re.compile(r"[A-Za-z0-9_.+-]+\.whl\Z")
_WHEEL_VERSION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.!+-]*\Z")
_POETRY_CORE_WHEEL: Final = re.compile(r"^poetry[-_.]+core-", re.IGNORECASE)
_AT_FDCWD: Final = -100
_RENAME_NOREPLACE: Final = 1


class ProvisionError(RuntimeError):
    """입력 provenance 또는 atomic publish 계약이 어긋났을 때 발생한다."""


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int


@dataclass(frozen=True)
class PoetryCoreSource:
    version: str
    files: tuple[Path, ...]


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        _assert_trusted_launcher()
        provision_wheelhouse(
            source_wheelhouse=arguments.source_wheelhouse,
            destination_wheelhouse=arguments.destination_wheelhouse,
        )
    except (OSError, ProvisionError, subprocess.SubprocessError) as exc:
        print(f"offline wheelhouse provisioning failed: {exc}", file=sys.stderr)
        return 1
    print("offline wheelhouse provisioned")
    return 0


def _assert_trusted_launcher() -> None:
    """root가 실행하는 provisioning source 자체도 root-locked artifact여야 한다."""

    script = Path(__file__)
    if not script.is_absolute():
        raise ProvisionError("provisioning script path must be absolute")
    try:
        metadata = script.lstat()
    except OSError as exc:
        raise ProvisionError("provisioning script cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ProvisionError("provisioning script is not root locked")
    _assert_locked_ancestors(script.parent)


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debian poetry-core로 root-owned offline wheelhouse를 발행한다."
    )
    parser.add_argument(
        "--source-wheelhouse",
        type=Path,
        default=_DEFAULT_SOURCE_WHEELHOUSE,
    )
    parser.add_argument(
        "--destination-wheelhouse",
        type=Path,
        default=_DEFAULT_DESTINATION_WHEELHOUSE,
    )
    return parser.parse_args(argv)


def provision_wheelhouse(
    *,
    source_wheelhouse: Path,
    destination_wheelhouse: Path,
) -> None:
    """검증된 source wheelhouse와 Debian package를 새 destination에 한 번만 발행한다."""

    if os.geteuid() != 0:
        raise ProvisionError("offline wheelhouse provisioning requires root")
    source = _canonical_locked_directory(source_wheelhouse)
    destination = _canonical_destination(destination_wheelhouse)
    if source == destination:
        raise ProvisionError("source and destination wheelhouse must differ")
    parent = _canonical_locked_directory(destination.parent)
    with _provision_lock(parent):
        _assert_no_staging_residue(parent)
        if destination.exists() or destination.is_symlink():
            raise ProvisionError("destination wheelhouse already exists")
        source_wheels = _snapshot_wheels(source)
        poetry_core = _verified_debian_poetry_core()
        staging = Path(tempfile.mkdtemp(prefix=".wheelhouse.stage.", dir=parent))
        published = False
        try:
            os.chmod(staging, 0o700)
            _copy_verified_wheels(source_wheels, staging)
            poetry_wheel = _write_poetry_core_wheel(staging, poetry_core)
            _verify_debian_package()
            _revalidate_wheel_snapshots(source_wheels)
            manifest = _manifest_payload(source_wheels, poetry_core, poetry_wheel)
            _write_private_json(staging / ".ktdm-wheelhouse-provenance.json", manifest)
            _fsync_directory(staging)
            os.chmod(staging, 0o755)
            _publish_without_replacing(staging, destination)
            published = True
            _fsync_directory(parent)
        finally:
            if not published:
                shutil.rmtree(staging, ignore_errors=True)


def _canonical_locked_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise ProvisionError("wheelhouse path must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProvisionError("wheelhouse path is unavailable") from exc
    if resolved != path:
        raise ProvisionError("wheelhouse path must be canonical")
    _assert_locked_directory(resolved)
    _assert_locked_ancestors(resolved)
    return resolved


def _canonical_destination(path: Path) -> Path:
    if not path.is_absolute():
        raise ProvisionError("destination wheelhouse path must be absolute")
    if path.name in {"", ".", ".."}:
        raise ProvisionError("destination wheelhouse name is invalid")
    parent = _canonical_locked_directory(path.parent)
    destination = parent / path.name
    if destination != path:
        raise ProvisionError("destination wheelhouse path must be canonical")
    return destination


def _assert_locked_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ProvisionError("wheelhouse directory cannot be inspected") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ProvisionError("wheelhouse directory is not root locked")


def _assert_locked_ancestors(path: Path) -> None:
    for current in (path, *path.parents):
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ProvisionError("wheelhouse ancestor cannot be inspected") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ProvisionError("wheelhouse ancestor is not root locked")


@contextmanager
def _provision_lock(parent: Path) -> Iterator[None]:
    path = parent / ".wheelhouse.provision.lock"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not isinstance(nofollow, int) or nofollow == 0:
        raise ProvisionError("O_NOFOLLOW is required for the wheelhouse lock")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | nofollow,
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ProvisionError("wheelhouse provision lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProvisionError("another wheelhouse provision is active") from exc
        yield
    except OSError as exc:
        raise ProvisionError("wheelhouse provision lock cannot be acquired") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_without_replacing(staging: Path, destination: Path) -> None:
    """Linux `renameat2`의 no-replace 보장으로 staging을 한 번만 publish한다."""

    library = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = library.renameat2
    except AttributeError as exc:
        raise ProvisionError("renameat2 no-replace support is required") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(staging),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ProvisionError("destination wheelhouse already exists")
    raise ProvisionError(
        "destination wheelhouse cannot be atomically published: "
        + os.strerror(error)
    )


def _assert_no_staging_residue(parent: Path) -> None:
    """crash 뒤의 managed staging은 자동 삭제·재발행하지 않고 operator에게 남긴다."""

    try:
        residue = sorted(parent.glob(".wheelhouse.stage.*"))
    except OSError as exc:
        raise ProvisionError("wheelhouse staging residue cannot be inspected") from exc
    if residue:
        raise ProvisionError("previous wheelhouse staging residue blocks provisioning")


def _snapshot_wheels(source: Path) -> tuple[tuple[Path, FileSnapshot], ...]:
    wheels: list[tuple[Path, FileSnapshot]] = []
    for path in sorted(source.glob("*.whl")):
        if not _WHEEL_NAME.fullmatch(path.name):
            raise ProvisionError("source wheel filename is invalid")
        if _POETRY_CORE_WHEEL.match(path.name):
            raise ProvisionError("source wheelhouse already supplies poetry-core wheel")
        wheels.append((path, _safe_file_snapshot(path)))
    if not wheels:
        raise ProvisionError("source wheelhouse has no wheels")
    return tuple(wheels)


def _safe_file_snapshot(path: Path) -> FileSnapshot:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ProvisionError("wheel cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ProvisionError("wheel is not root locked")
    return _snapshot(metadata)


def _snapshot(metadata: os.stat_result) -> FileSnapshot:
    return FileSnapshot(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        nlink=metadata.st_nlink,
        size=metadata.st_size,
    )


def _read_exact_file(path: Path, expected: FileSnapshot) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not isinstance(nofollow, int) or nofollow == 0:
        raise ProvisionError("O_NOFOLLOW is required for wheel inputs")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
        opened = _snapshot(os.fstat(descriptor))
        if opened != expected:
            raise ProvisionError("wheel changed while opening")
        contents = bytearray()
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            contents.extend(chunk)
        if _safe_file_snapshot(path) != expected:
            raise ProvisionError("wheel changed while reading")
        return bytes(contents)
    except OSError as exc:
        raise ProvisionError("wheel cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _copy_verified_wheels(
    source_wheels: Iterable[tuple[Path, FileSnapshot]], staging: Path
) -> None:
    for source, snapshot in source_wheels:
        payload = _read_exact_file(source, snapshot)
        target = staging / source.name
        _write_exact_file(target, payload, mode=0o644)


def _revalidate_wheel_snapshots(
    source_wheels: Iterable[tuple[Path, FileSnapshot]],
) -> None:
    for source, expected in source_wheels:
        if _safe_file_snapshot(source) != expected:
            raise ProvisionError("source wheelhouse changed during provisioning")


def _verified_debian_poetry_core() -> PoetryCoreSource:
    _verify_debian_package()
    root = _canonical_locked_directory(_SYSTEM_DIST_PACKAGES)
    listed = _debian_package_files()
    metadata_paths = [
        path
        for path in listed
        if path.parent.name.startswith("poetry_core-") and path.name == "METADATA"
    ]
    if len(metadata_paths) != 1:
        raise ProvisionError("Debian poetry-core metadata is missing or ambiguous")
    metadata_path = metadata_paths[0]
    metadata = BytesParser(policy=default).parsebytes(
        _read_debian_file(metadata_path)
    )
    if metadata["Name"] != "poetry-core":
        raise ProvisionError("Debian poetry-core metadata name is invalid")
    version = metadata["Version"]
    if not isinstance(version, str) or _WHEEL_VERSION.fullmatch(version) is None:
        raise ProvisionError("Debian poetry-core version is invalid")
    dist_info = root / f"poetry_core-{version}.dist-info"
    required = {
        root / "poetry/core/__init__.py",
        dist_info / "METADATA",
        dist_info / "WHEEL",
    }
    files = tuple(
        sorted(
            path
            for path in listed
            if path != dist_info / "RECORD" and "__pycache__" not in path.parts
        )
    )
    if not required.issubset(files):
        raise ProvisionError("Debian poetry-core package is incomplete")
    wheel = BytesParser(policy=default).parsebytes(_read_debian_file(dist_info / "WHEEL"))
    if wheel["Root-Is-Purelib"] != "true" or "py3-none-any" not in wheel.get_all(
        "Tag", []
    ):
        raise ProvisionError("Debian poetry-core wheel metadata is incompatible")
    return PoetryCoreSource(version=version, files=files)


def _verify_debian_package() -> None:
    result = _run_debian_command([_DPKG, "--verify", _DEBIAN_PACKAGE])
    if result.returncode != 0 or result.stdout.strip() or result.stderr.strip():
        raise ProvisionError("Debian poetry-core package verification failed")
    status = _run_debian_command(
        [_DPKG_QUERY, "-W", "-f=${Status}", _DEBIAN_PACKAGE]
    )
    if status.returncode != 0 or status.stdout.strip() != "install ok installed":
        raise ProvisionError("Debian poetry-core package is not installed")


def _debian_package_files() -> tuple[Path, ...]:
    result = _run_debian_command([_DPKG_QUERY, "-L", _DEBIAN_PACKAGE], check=True)
    root = _SYSTEM_DIST_PACKAGES.resolve(strict=True)
    files: list[Path] = []
    for line in result.stdout.splitlines():
        path = Path(line)
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        files.append(path)
    if not files:
        raise ProvisionError("Debian poetry-core package has no Python files")
    return tuple(files)


def _run_debian_command(
    arguments: list[str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """fixed binary와 ambient-free locale/environment로 dpkg evidence만 읽는다."""

    return subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=check,
        env=_DEBIAN_COMMAND_ENV,
    )


def _read_debian_file(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ProvisionError("Debian poetry-core file is not root locked")
    return _read_exact_file(path, _snapshot(metadata))


def _write_poetry_core_wheel(staging: Path, source: PoetryCoreSource) -> Path:
    name = f"poetry_core-{source.version}-py3-none-any.whl"
    target = staging / name
    if target.exists() or target.is_symlink():
        raise ProvisionError("source wheelhouse already supplies poetry-core wheel")
    records: list[tuple[str, str, str]] = []
    with tempfile.NamedTemporaryFile(dir=staging, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
            for path in source.files:
                member = path.relative_to(_SYSTEM_DIST_PACKAGES).as_posix()
                payload = _read_debian_file(path)
                wheel.writestr(member, payload)
                records.append(
                    (
                        member,
                        "sha256=" + _sha256_record(payload),
                        str(len(payload)),
                    )
                )
            record_member = f"poetry_core-{source.version}.dist-info/RECORD"
            records.append((record_member, "", ""))
            record = "".join(",".join(row) + "\n" for row in records).encode(
                "utf-8"
            )
            wheel.writestr(record_member, record)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target)
        _fsync_file(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target


def _sha256_record(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(
        b"="
    ).decode("ascii")


def _manifest_payload(
    source_wheels: Iterable[tuple[Path, FileSnapshot]],
    poetry_core: PoetryCoreSource,
    poetry_wheel: Path,
) -> dict[str, object]:
    source_entries = [
        {
            "name": path.name,
            "sha256": hashlib.sha256(_read_exact_file(path, snapshot)).hexdigest(),
        }
        for path, snapshot in source_wheels
    ]
    poetry_payload = _read_exact_file(poetry_wheel, _safe_file_snapshot(poetry_wheel))
    return {
        "schema": "ktdm.offline-wheelhouse-provenance.v1",
        "debian_package": _DEBIAN_PACKAGE,
        "poetry_core_version": poetry_core.version,
        "poetry_core_wheel_sha256": hashlib.sha256(poetry_payload).hexdigest(),
        "source_wheels": source_entries,
    }


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _write_exact_file(path, encoded + b"\n", mode=0o644)


def _write_exact_file(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            mode,
        )
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view)
            view = view[count:]
        os.fsync(descriptor)
    except OSError as exc:
        raise ProvisionError("wheelhouse output cannot be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        os.fsync(descriptor)
    except OSError as exc:
        raise ProvisionError("wheelhouse output cannot be synchronized") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        os.fsync(descriptor)
    except OSError as exc:
        raise ProvisionError("wheelhouse directory cannot be synchronized") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
