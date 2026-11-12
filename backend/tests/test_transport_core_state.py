"""고정 state subtree의 tar·metadata·lease 경계와 자식 FD 수명."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services import transport_core_state as state
from kor_travel_docker_manager.services.service_release import ServiceReleaseError


def tar_fixture(path, extras=None):
    rows = [
        ("state", tarfile.DIRTYPE, b""),
        ("state/artifacts", tarfile.DIRTYPE, b""),
        ("state/artifacts/data", tarfile.REGTYPE, b"retained\x00data"),
    ]
    rows.extend(extras or [])
    with tarfile.open(path, "w") as archive:
        for name, kind, data in rows:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.uid, item.gid, item.mode = 1234, 2345, 0o750 if kind == tarfile.DIRTYPE else 0o640
            item.size = len(data) if kind == tarfile.REGTYPE else 0
            item.linkname = "/outside" if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE} else ""
            archive.addfile(item, io.BytesIO(data) if data else None)
    path.chmod(0o600)
    return path


def test_archive_semantics_preserve_uid_gid_mode_and_content(tmp_path):
    result = state.archive_semantic(tar_fixture(tmp_path / "state.tar"))
    assert result["root"] == {"uid": 1234, "gid": 2345, "mode": 0o750}
    assert result["entries"]["artifacts/data"] == {
        "uid": 1234,
        "gid": 2345,
        "mode": 0o640,
        "kind": "file",
        "bytes": 13,
        "sha256": hashlib.sha256(b"retained\x00data").hexdigest(),
    }
    assert not (tmp_path / "artifacts").exists(), "tar content must never be extracted on host"


@pytest.mark.parametrize(
    "name,kind",
    [
        ("/state/bad", tarfile.REGTYPE),
        ("state/../bad", tarfile.REGTYPE),
        ("state/./bad", tarfile.REGTYPE),
        ("state//bad", tarfile.REGTYPE),
        ("other/data", tarfile.REGTYPE),
        ("state/artifacts/data", tarfile.REGTYPE),
        ("state/artifacts", tarfile.DIRTYPE),
        ("state/link", tarfile.SYMTYPE),
        ("state/hard", tarfile.LNKTYPE),
        ("state/pipe", tarfile.FIFOTYPE),
        ("state/device", tarfile.CHRTYPE),
        ("state/a\\b", tarfile.REGTYPE),
        ("state/missing/child", tarfile.REGTYPE),
    ],
)
def test_malicious_or_ambiguous_archive_refused(tmp_path, name, kind):
    archive = tar_fixture(tmp_path / "state.tar", [(name, kind, b"untrusted")])
    with pytest.raises(ServiceReleaseError):
        state.archive_semantic(archive)


def test_archive_setuid_refused(tmp_path):
    path = tmp_path / "state.tar"
    with tarfile.open(path, "w") as archive:
        item = tarfile.TarInfo("state")
        item.type, item.mode = tarfile.DIRTYPE, 0o4755
        archive.addfile(item)
    with pytest.raises(ServiceReleaseError):
        state.archive_semantic(path)


@pytest.mark.parametrize(
    "field,value", [("uid", -1), ("mode", 0o1777), ("mtime_ns", None), ("ctime_ns", True)]
)
def test_fingerprint_metadata_must_be_complete_and_safe(tmp_path, field, value):
    root = tmp_path / "state"
    root.mkdir()
    (root / "data").write_bytes(b"retained")
    result = state.scan_state(root)
    result["root"][field] = value
    with pytest.raises(ServiceReleaseError):
        state.validate_fingerprint(result)


def test_runtime_copy_semantics_separate_from_original_freshness(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    (root / "data").write_bytes(b"retained")
    original = state.scan_state(root)
    state.validate_fingerprint(original)
    copied = copy.deepcopy(original)
    for row in [copied["root"], *copied["entries"].values()]:
        row["mtime_ns"] += 1
        row["ctime_ns"] += 2
    assert original != copied
    assert state.semantic(original) == state.semantic(copied)
    copied["entries"]["data"]["mode"] ^= 0o100
    assert state.semantic(original) != state.semantic(copied)


def test_snapshot_exclusive_and_bounded_no_existing_file_clobber(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.tar"
    path.write_bytes(b"existing")
    client = SimpleNamespace(api=SimpleNamespace(get_archive=lambda *a: (iter([b"new"]), {})))
    with pytest.raises(FileExistsError):
        state.save_archive(client, "old", path)
    assert path.read_bytes() == b"existing"
    monkeypatch.setattr(state, "MAX_ARCHIVE_BYTES", 2)
    with pytest.raises(ServiceReleaseError):
        state.save_archive(client, "old", tmp_path / "partial.tar")
    assert stat.S_IMODE((tmp_path / "partial.tar").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "change", ["running", "wrong_id", "wrong_image", "wrong_role", "mount", "nested_mount"]
)
def test_restore_requires_exact_unstarted_cs_before_any_write(tmp_path, change):
    record = {
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "Config": {
            "Labels": {
                "com.docker.compose.project": "kor-travel-transport",
                "com.docker.compose.service": "dagster-code-server",
            }
        },
        "State": {"Running": False, "Status": "created"},
        "Mounts": [],
    }
    if change == "running":
        record["State"].update(Running=True, Status="running")
    elif change == "wrong_id":
        record["Id"] = "c" * 64
    elif change == "wrong_image":
        record["Image"] = "sha256:" + "c" * 64
    elif change == "wrong_role":
        record["Config"]["Labels"]["com.docker.compose.service"] = "backend"
    elif change == "mount":
        record["Mounts"] = [{"Destination": "/opt/dagster"}]
    elif change == "nested_mount":
        record["Mounts"] = [{"Destination": "/opt/dagster/state/artifacts"}]
    put = Mock()
    client = SimpleNamespace(
        api=SimpleNamespace(inspect_container=lambda cid: record, put_archive=put)
    )
    snapshot = tar_fixture(tmp_path / "state.tar")
    with pytest.raises(ServiceReleaseError):
        state.restore_state(
            client, "a" * 64, "sha256:" + "b" * 64, snapshot, state.archive_semantic(snapshot)
        )
    put.assert_not_called()


def test_state_child_uses_installed_isolated_module_and_inherited_lease(monkeypatch, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"success":true}', ""), []

    monkeypatch.setattr(core.frozen, "_run_mutation", run)
    core._state_child("snapshot", "a" * 64, "sha256:" + "b" * 64, tmp_path, {}, 91)
    argv, options = calls[0]
    assert argv == [
        sys.executable,
        "-I",
        "-m",
        "kor_travel_docker_manager.services.transport_core_state",
    ]
    assert options["additional_fds"] == (91,)
    assert "PYTHONPATH" not in options["env"] and "timeout" not in options
    assert json.loads(options["input"])["operation"] == "snapshot"


def test_lease_nofollow_identity_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_lease_ancestors", lambda p: None)
    path = tmp_path / "existing.lock"
    path.write_text("")
    path.chmod(0o600)
    with state.project_lease(path) as fd:
        state.check_lease(fd, path)
        replacement = tmp_path / "replacement"
        replacement.write_text("")
        replacement.chmod(0o600)
        replacement.replace(path)
        with pytest.raises(ServiceReleaseError):
            state.check_lease(fd, path)
    linked = tmp_path / "link"
    linked.symlink_to(path)
    with pytest.raises(OSError):
        with state.project_lease(linked):
            pass
    path.chmod(0o666)
    with pytest.raises(ServiceReleaseError):
        with state.project_lease(path):
            pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux FD와 signal 계약")
@pytest.mark.parametrize("termination", [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
def test_parent_termination_keeps_both_g_and_project_lease(tmp_path, termination):
    g, lease, ready, done = [tmp_path / name for name in ["G", "lease", "ready", "done"]]
    script = r"""
import fcntl,json,sys
from pathlib import Path
from kor_travel_docker_manager.services import service_release_compose as frozen
G,lease,ready,done=map(Path,sys.argv[1:])
frozen.GLOBAL_MUTATION_LOCK_PATH=G
child="import time;from pathlib import Path;Path(%r).touch();end=time.monotonic()+12;\nwhile not Path(%r).exists() and time.monotonic()<end:time.sleep(.02)" % (str(ready),str(done))
with G.open('w') as g, lease.open('w') as l:
 fcntl.flock(g,fcntl.LOCK_EX);fcntl.flock(l,fcntl.LOCK_EX)
 result,signals=frozen._run_mutation([sys.executable,'-c',child],additional_fds=(l.fileno(),),capture_output=True,text=True,check=False)
 print(json.dumps({'exit':result.returncode,'signals':signals}))
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(g), str(lease), str(ready), str(done)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def busy(path):
        with path.open("r") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True

    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline and parent.poll() is None:
            time.sleep(0.02)
        assert ready.exists(), parent.communicate(timeout=1)
        os.kill(parent.pid, termination)
        if termination == signal.SIGKILL:
            assert parent.wait(timeout=3) == -signal.SIGKILL
        else:
            time.sleep(0.1)
            assert parent.poll() is None
        assert busy(g) and busy(lease), "state/compose child must retain BOTH descriptors"
        done.touch()
        if termination != signal.SIGKILL:
            stdout, stderr = parent.communicate(timeout=5)
            assert parent.returncode == 0, stderr
            assert signal.Signals(termination).name in stdout
        deadline = time.monotonic() + 5
        while (busy(g) or busy(lease)) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not busy(g) and not busy(lease)
    finally:
        done.touch()
        if parent.poll() is None:
            try:
                parent.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                parent.kill()
                parent.communicate(timeout=3)


def test_unverified_pax_xattrs_refused(tmp_path):
    path = tmp_path / "state.tar"
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        root = tarfile.TarInfo("state")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        root.pax_headers = {"SCHILY.xattr.security.capability": "unverified"}
        archive.addfile(root)
    with pytest.raises(ServiceReleaseError, match="확장 metadata"):
        state.archive_semantic(path)


@pytest.mark.parametrize(
    "mode,link", [(0x080001FF, "/elsewhere"), (0x800001ED, "/elsewhere"), (0o755, "")]
)
def test_state_parent_must_not_be_symlink(mode, link):
    client = SimpleNamespace(
        api=SimpleNamespace(
            get_archive=lambda *args: (iter([]), {"mode": mode, "linkTarget": link})
        )
    )
    with pytest.raises(ServiceReleaseError):
        state._directory_stat(client, "created-cid", "/opt")
