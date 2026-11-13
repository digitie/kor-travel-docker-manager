"""Transport의 고정 state 경로만 새 미시작 CS writable layer로 인계한다."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import stat
import sys
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import docker

from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

STATE_PATH = "/opt/dagster/state"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def scan_state(root: Path) -> dict:
    """Transport prebuilt-v1과 동일한 내용/metadata/freshness 계약이다."""
    import hashlib
    import os
    import stat
    from pathlib import Path

    if not root.is_dir() or root.is_symlink():
        raise ValueError("state root absent or unsafe")

    def stable(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_nlink,
        )

    root_stat = root.stat()
    rows = {}
    pending = [root]
    while pending:
        parent = pending.pop()
        for item in os.scandir(parent):
            path, info = Path(item.path), item.stat(follow_symlinks=False)
            value = {
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": stat.S_IMODE(info.st_mode),
                "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns,
            }
            if stat.S_ISDIR(info.st_mode):
                value["kind"] = "directory"
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block)
                    if stable(os.fstat(stream.fileno())) != stable(info):
                        raise ValueError("state file changed during scan")
                value.update(kind="file", bytes=info.st_size, sha256=digest.hexdigest())
            elif stat.S_ISLNK(info.st_mode):
                value.update(kind="symlink", target=os.readlink(path))
            else:
                raise ValueError("special state file refused")
            if stable(path.lstat()) != stable(info):
                raise ValueError("state entry changed during scan")
            rows[path.relative_to(root).as_posix()] = value
    if stable(root.stat()) != stable(root_stat):
        raise ValueError("state root changed during scan")
    return {
        "root": {
            "uid": root_stat.st_uid,
            "gid": root_stat.st_gid,
            "mode": stat.S_IMODE(root_stat.st_mode),
            "mtime_ns": root_stat.st_mtime_ns,
            "ctime_ns": root_stat.st_ctime_ns,
        },
        "entries": rows,
    }


def state_manifest(fingerprint: dict) -> dict:
    return {
        key: {k: v for k, v in row.items() if k not in {"mtime_ns", "ctime_ns"}}
        for key, row in fingerprint["entries"].items()
    }


def semantic(fingerprint: dict) -> dict:
    return {
        "root": {k: fingerprint["root"][k] for k in ("uid", "gid", "mode")},
        "entries": state_manifest(fingerprint),
    }


def _safe_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
    ):
        raise ServiceReleaseError("state archive 경로가 안전하지 않습니다")
    return name.rstrip("/")


def validate_fingerprint(value: Any) -> None:
    try:
        if set(value) != {"root", "entries"} or not isinstance(value["entries"], dict):
            raise ValueError
        root = value["root"]
        if set(root) != {"uid", "gid", "mode", "mtime_ns", "ctime_ns"}:
            raise ValueError
        for key, row in [(None, root), *value["entries"].items()]:
            if key is not None:
                _safe_name(key)
                if key != str(PurePosixPath(key)) or row["kind"] not in {"file", "directory"}:
                    raise ValueError
                fields = {"uid", "gid", "mode", "mtime_ns", "ctime_ns", "kind"}
                if row["kind"] == "file":
                    fields |= {"bytes", "sha256"}
                    release._image_id("sha256:" + row["sha256"])
                    if type(row["bytes"]) is not int or row["bytes"] < 0:
                        raise ValueError
                if set(row) != fields:
                    raise ValueError
                parent = str(PurePosixPath(key).parent)
                if parent != "." and value["entries"].get(parent, {}).get("kind") != "directory":
                    raise ValueError
            if (
                any(
                    type(row.get(k)) is not int or row[k] < 0
                    for k in ("uid", "gid", "mode", "mtime_ns", "ctime_ns")
                )
                or row["mode"] > 0o777
            ):
                raise ValueError
        if (
            len(value["entries"]) > 100000
            or sum(r.get("bytes", 0) for r in value["entries"].values()) > MAX_ARCHIVE_BYTES // 2
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceReleaseError(
            "고정 state fingerprint 형식 또는 범위가 올바르지 않습니다"
        ) from exc


def archive_semantic(path: Path) -> dict:
    """데이터를 host에 풀지 않고 고정 state subtree의 의미만 읽는다."""
    entries, root = {}, None
    total = 0
    with tarfile.open(path, mode="r:") as archive:
        seen = set()
        for member in archive:
            name = _safe_name(member.name)
            if name in seen or (name != "state" and not name.startswith("state/")):
                raise ServiceReleaseError("state archive 중복 또는 범위 밖 항목입니다")
            seen.add(name)
            if (
                len(seen) > 100001
                or not (member.isdir() or member.isfile())
                or member.mode & ~0o777
                or member.uid < 0
                or member.gid < 0
            ):
                raise ServiceReleaseError("state archive 종류 또는 권한이 안전하지 않습니다")
            if set(member.pax_headers) - {
                "path",
                "mtime",
                "atime",
                "ctime",
                "uid",
                "gid",
                "size",
                "uname",
                "gname",
            }:
                raise ServiceReleaseError("state archive에 검증하지 않은 확장 metadata가 있습니다")
            row = {"uid": member.uid, "gid": member.gid, "mode": member.mode}
            if name == "state":
                if not member.isdir():
                    raise ServiceReleaseError("state archive root가 디렉터리가 아닙니다")
                root = row
                continue
            row["kind"] = "directory" if member.isdir() else "file"
            if member.isfile():
                total += member.size
                if member.size < 0 or total > MAX_ARCHIVE_BYTES // 2:
                    raise ServiceReleaseError("state archive 크기 상한을 넘었습니다")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ServiceReleaseError("state archive 파일을 읽을 수 없습니다")
                hashed = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    hashed.update(block)
                row.update(bytes=member.size, sha256=hashed.hexdigest())
            entries[name[6:]] = row
    if root is None:
        raise ServiceReleaseError("state archive root가 없습니다")
    for name in entries:
        parent = str(PurePosixPath(name).parent)
        if parent != "." and entries.get(parent, {}).get("kind") != "directory":
            raise ServiceReleaseError("state archive 부모 디렉터리가 없습니다")
    return {"root": root, "entries": entries}


def save_archive(client: Any, cid: str, path: Path) -> None:
    chunks, _metadata = client.api.get_archive(cid, STATE_PATH)
    count = 0
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            for chunk in chunks:
                count += len(chunk)
                if count > MAX_ARCHIVE_BYTES:
                    raise ServiceReleaseError("state archive 전송 크기 상한을 넘었습니다")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    finally:
        close = getattr(chunks, "close", None)
        if close:
            close()


def _created_cs(client: Any, cid: str, image_id: str, *,
                project: str = "kor-travel-transport", service: str = "dagster-code-server") -> dict:
    if (project, service) != ("kor-travel-transport", "dagster-code-server"):
        from kor_travel_docker_manager.services.registry import load_targets_config

        group = load_targets_config().get("service_releases", {}).get("weather", {}).get("groups", {}).get("main", {})
        if (service != "kor-travel-weather-dagster-code-server" or project != group.get("project")
                or service not in group.get("runtime_services", [])):
            raise ServiceReleaseError("등록된 Transport/Weather CS 목적지만 허용합니다")
    record = client.api.inspect_container(cid)
    labels = record.get("Config", {}).get("Labels", {})
    if (
        record.get("Id") != cid
        or record.get("Image") != image_id
        or record.get("State", {}).get("Status") != "created"
        or record["State"].get("Running") is not False
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
    ):
        raise ServiceReleaseError("state 목적지가 승인된 미시작 Transport CS가 아닙니다")
    if not isinstance(record.get("Mounts"), list):
        raise ServiceReleaseError("새 CS mount 관측이 불완전합니다")
    for mount in record["Mounts"]:
        dest = mount.get("Destination", "")
        if (
            not dest.startswith("/")
            or STATE_PATH == dest
            or STATE_PATH.startswith(dest.rstrip("/") + "/")
            or dest.startswith(STATE_PATH + "/")
        ):
            raise ServiceReleaseError("state 경로를 덮는 mount가 있습니다")
    return record


def _directory_stat(client: Any, cid: str, path: str, *, missing_ok: bool = False) -> bool:
    try:
        chunks, info = client.api.get_archive(cid, path)
        close = getattr(chunks, "close", None)
        if close:
            close()
        if not info["mode"] & 0x80000000 or info.get("linkTarget"):
            raise ServiceReleaseError("새 CS state 부모는 symlink 아닌 디렉터리여야 합니다")
        return True
    except docker.errors.NotFound:
        if not missing_ok:
            raise ServiceReleaseError("새 CS에 고정 /opt 디렉터리가 없습니다") from None
        return False


def restore_state(client: Any, cid: str, image_id: str, snapshot: Path, expected: dict, *,
                  project: str = "kor-travel-transport", service: str = "dagster-code-server") -> None:
    _created_cs(client, cid, image_id, project=project, service=service)
    if archive_semantic(snapshot) != expected:
        raise ServiceReleaseError("보관 state와 승인 manifest가 다릅니다")
    # 부모가 없을 때만 고정 /opt/dagster header를 만든다. 이미지·host 파일은 바꾸지 않는다.
    _directory_stat(client, cid, "/opt")
    parent_exists = _directory_stat(client, cid, "/opt/dagster", missing_ok=True)
    payload = snapshot.parent / "restore.tar"
    fd = os.open(payload, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output, tarfile.open(fileobj=output, mode="w|") as target:
        if not parent_exists:
            parent = tarfile.TarInfo("dagster")
            parent.type, parent.mode = tarfile.DIRTYPE, 0o755
            target.addfile(parent)
        with tarfile.open(snapshot, mode="r:") as source:
            for member in source:
                member.name = "dagster/" + _safe_name(member.name)
                # PAX path가 새 prefix를 다시 덮지 않게 표준 header에서 재생성한다.
                member.pax_headers = {
                    k: v for k, v in member.pax_headers.items() if k in {"mtime", "atime", "ctime"}
                }
                member.uname = member.gname = ""

                target.addfile(member, source.extractfile(member) if member.isfile() else None)
    _created_cs(client, cid, image_id, project=project, service=service)
    with payload.open("rb") as stream:
        if client.api.put_archive(cid, "/opt", stream) is not True:
            raise ServiceReleaseError("새 CS state 복원이 실패했습니다")
    readback = snapshot.parent / "restored-readback.tar"
    save_archive(client, cid, readback)
    if archive_semantic(readback) != expected:
        raise ServiceReleaseError("새 미시작 CS state readback이 다릅니다")
    _created_cs(client, cid, image_id, project=project, service=service)


def check_lease(fd: int, path: Path) -> None:
    _lease_ancestors(path)
    before, current = os.fstat(fd), path.lstat()
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or current.st_mode & 0o022
        or current.st_uid not in {0, path.parent.stat().st_uid}
        or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ServiceReleaseError("기존 Transport project lease가 안전하지 않습니다")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _lease_ancestors(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise ServiceReleaseError("Transport project lease 경로가 안전하지 않습니다")
    child = path
    for parent in path.parents:
        s = parent.lstat()
        sticky_mount = False
        if s.st_mode & 0o022:
            c = child.lstat()
            sticky_mount = bool(
                s.st_uid == 0 and s.st_mode & stat.S_ISVTX and parent.is_mount()
                and stat.S_ISDIR(c.st_mode) and c.st_uid == 0 and not c.st_mode & 0o022
            )
        if not stat.S_ISDIR(s.st_mode) or (s.st_mode & 0o022 and not sticky_mount):
            raise ServiceReleaseError("Transport project lease 상위 경로가 안전하지 않습니다")
        child = parent


@contextmanager
def project_lease(path: Path):
    _lease_ancestors(path)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        check_lease(fd, path)
        yield fd
    finally:
        os.close(fd)  # LOCK_UN은 상속한 자식의 open-description 잠금도 풀므로 쓰지 않는다.


@contextmanager
def _transfer_signals():
    """state RPC가 끝나기 전에 자식만 종료되어 G/lease를 놓지 않게 한다."""
    interrupted = []

    def defer(signum, frame):
        interrupted.append(signal.Signals(signum).name)

    previous = {sig: signal.signal(sig, defer) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield interrupted
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _worker() -> None:
    """공식 설치 package만 -I -m으로 실행한다. 임의 source/helper를 실행하지 않는다."""
    from kor_travel_docker_manager.services import service_release_compose as frozen
    from kor_travel_docker_manager.services.registry import load_targets_config

    frozen._mutation_lock_fd()
    request = json.loads(sys.stdin.read(32 * 1024 * 1024 + 1))
    lease = Path(load_targets_config()["targets"]["transport"]["core_candidate"]["project_lease"])
    check_lease(request["lease_fd"], lease)
    stage = Path(request["stage"])
    release._trusted_ancestors(stage)
    if stat.S_IMODE(stage.stat().st_mode) != 0o700 or os.geteuid() != 0:
        raise ServiceReleaseError("state 인계 private stage가 안전하지 않습니다")
    snapshot = stage / "state.tar"
    validate_fingerprint(request["fingerprint"])
    expected = semantic(request["fingerprint"])
    # mutation RPC의 host timeout으로 daemon만 쓰는 상태를 남기지 않는다.
    with _transfer_signals() as interrupted:
        client = docker.from_env(timeout=None)
        try:
            if request["operation"] == "snapshot":
                record = client.api.inspect_container(request["cid"])
                if record.get("Id") != request["cid"] or not record.get("State", {}).get("Running"):
                    raise ServiceReleaseError("이전 CS가 변경되었습니다")
                save_archive(client, request["cid"], snapshot)
                if archive_semantic(snapshot) != expected:
                    raise ServiceReleaseError("이전 CS archive와 승인 state가 다릅니다")
            elif request["operation"] == "restore":
                restore_state(client, request["cid"], request["image_id"], snapshot, expected)
            else:
                raise ServiceReleaseError("알 수 없는 state 인계 단계입니다")
            check_lease(request["lease_fd"], lease)
            if interrupted:
                raise ServiceReleaseError(
                    "state 인계가 완료되었지만 중단 요청으로 발행하지 않습니다"
                )
            print(json.dumps({"success": True}))
        finally:
            client.close()


if __name__ == "__main__":
    try:
        _worker()
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
