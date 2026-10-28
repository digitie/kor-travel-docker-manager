"""서비스 release의 관측 전용 등록·신뢰 경계·원자 교체 회귀."""

from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kor_travel_docker_manager.services import service_release as release

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
C = "sha256:" + "c" * 64
SHA = "d" * 40


class Docker:
    """읽기 API만 제공하므로 변경 API 호출은 테스트를 실패시킨다."""

    def __init__(self):
        self.records = {"api": [self.container(A)]}
        self.metadata = {image: {
            "Id": image, "Created": "2026-10-28T00:00:00.123456789Z",
            "Config": {"Labels": {"org.opencontainers.image.revision": SHA},
                       "Env": ["SECRET=must-not-return"]},
        } for image in (A, B, C)}
        self.calls = []
        self.on_list = None
        self.containers = SimpleNamespace(list=self.list)
        self.images = SimpleNamespace(get=self.get)

    @staticmethod
    def container(image):
        return {"Id": "container-1", "Image": image,
                "Config": {"Healthcheck": {"Test": ["CMD", "probe"]},
                           "Env": ["SECRET=must-not-return"]},
                "State": {"Running": True, "StartedAt": "2026-10-28T00:00:00Z",
                          "Health": {"Status": "healthy"}}}

    def list(self, *, all, filters):
        assert all is True
        self.calls.append(("list", filters))
        if self.on_list:
            self.on_list(self)
        name = filters["label"][1].split("=", 1)[1]
        return [SimpleNamespace(attrs=copy.deepcopy(item)) for item in self.records.get(name, [])]

    def get(self, image):
        self.calls.append(("image", image))
        return SimpleNamespace(attrs=copy.deepcopy(self.metadata[image]))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "_OWNER_UID", os.getuid())
    # /tmp 상위의 sticky/world-write는 운영 신뢰 경로가 아니다. 별도 테스트로 검사한다.
    monkeypatch.setattr(release, "_trusted_ancestors", release._directory)
    locked = []

    @contextmanager
    def lock():
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()

    monkeypatch.setattr(release, "manager_mutation_lock", lock)
    root = tmp_path / "releases"
    root.mkdir(mode=0o755)
    profile = {"service": "sample", "groups": {"core": {
        "project": "shared", "working_dir": str(tmp_path),
        "config_files": ["compose.yml"], "env_file": None,
        "runtime_services": ["api"], "oneshot_images": {"migrate": B},
    }}}
    client = Docker()
    original = client.list

    def guarded(**kwargs):
        # adopt의 Docker 관측은 G 획득 뒤에만 가능하다.
        assert locked
        return original(**kwargs)

    client.containers.list = guarded
    return root, profile, client


def adopt(setup, **kwargs):
    root, profile, client = setup
    return release.adopt_release(profile, root=root, client=client, **kwargs)


def read(setup):
    return release.read_current_release("sample", root=setup[0])


def verify(setup):
    root, _, client = setup
    client.containers.list = client.list
    return release.verify_release("sample", root=root, client=client)


def test_adopt_read_verify_and_no_raw_metadata(setup):
    root, profile, client = setup
    result = adopt(setup)
    assert read(setup) == result
    assert result["source_sha"] == SHA
    assert result["previous"] is None
    assert "SECRET" not in json.dumps(result)
    leaf = Path(result["release_directory"])
    assert (root / "current" / "sample").readlink() == leaf
    assert leaf.parent == root
    assert (leaf.stat().st_mode & 0o777) == 0o711
    assert (root / "current").stat().st_mode & 0o777 == 0o755
    for name in ("images.yml", "manifest.json"):
        assert (leaf / name).stat().st_mode & 0o777 == 0o644
    assert "release_directory" not in json.loads((leaf / "manifest.json").read_text())
    overlay = yaml.safe_load((leaf / "images.yml").read_text())
    assert overlay == {"services": {"api": {"image": A}, "migrate": {"image": B}}}
    assert not any("migrate" in str(call[1]) for call in client.calls if call[0] == "list")
    assert verify(setup)["result"] == "healthy"
    assert profile["groups"]["core"]["config_files"] == ["compose.yml"]


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_duplicate_runtime_is_rejected(setup, count):
    root, _, client = setup
    client.records["api"] = [client.container(A) for _ in range(count)]
    with pytest.raises(release.ServiceReleaseError, match="정확히 하나"):
        adopt(setup)
    assert not (root / "current" / "sample").exists()


@pytest.mark.parametrize("health", ["starting", "unhealthy", None])
def test_enabled_health_requires_healthy(setup, health):
    setup[2].records["api"][0]["State"]["Health"] = {"Status": health}
    with pytest.raises(release.ServiceReleaseError):
        adopt(setup)


@pytest.mark.parametrize("check", [None, {"Test": ["NONE"]}])
def test_no_healthcheck_or_explicit_none_requires_only_running(setup, check):
    container = setup[2].records["api"][0]
    container["Config"]["Healthcheck"] = check
    container["State"].pop("Health")
    assert adopt(setup)["health"]["result"] == "healthy"


def test_stopped_container_and_missing_oneshot_image_rejected(setup):
    client = setup[2]
    client.records["api"][0]["State"]["Running"] = False
    with pytest.raises(release.ServiceReleaseError):
        adopt(setup)
    client.records["api"][0]["State"]["Running"] = True
    del client.metadata[B]
    with pytest.raises(release.ServiceReleaseError, match="one-shot"):
        adopt(setup)


@pytest.mark.parametrize("revision", ["e" * 40, None, "tag-is-not-source-sha"])
def test_mixed_or_unknown_source_is_not_guessed(setup, revision):
    setup[2].metadata[B]["Config"]["Labels"]["org.opencontainers.image.revision"] = revision
    result = adopt(setup)
    assert result["source_sha"] is None
    assert result["images"]["api"]["source_sha"] == SHA


def test_verify_reports_expected_actual_image_drift(setup):
    adopt(setup)
    setup[2].records["api"][0]["Image"] = C
    report = verify(setup)
    assert report["result"] == "drift"
    assert report["images"]["api"] == {"expected": A, "actual": C}


def test_idempotent_and_explicit_replace_preserve_previous(setup):
    first = adopt(setup)
    assert adopt(setup) == first
    assert len(list(setup[0].glob("sample-adopt-*"))) == 1
    setup[2].records["api"][0]["Image"] = C
    with pytest.raises(release.ServiceReleaseError, match="replace_current"):
        adopt(setup)
    assert read(setup) == first
    second = adopt(setup, replace_current=True)
    assert second["previous"] == first["release_directory"]
    assert second["images"]["api"]["id"] == C
    assert adopt(setup) == second


@pytest.mark.parametrize("change", ["missing", "new", "coordinates", "role"])
def test_replace_cannot_silently_change_layout(setup, change):
    first = adopt(setup)
    group = setup[1]["groups"]["core"]
    if change == "missing":
        group["oneshot_images"] = {}
    elif change == "new":
        group["runtime_services"].append("new-api")
    elif change == "coordinates":
        group["project"] = "other"
    else:
        group["runtime_services"] = []
        group["oneshot_images"]["api"] = A
    setup[2].calls.clear()
    with pytest.raises(release.ServiceReleaseError, match="좌표 또는 서비스 역할"):
        adopt(setup, replace_current=True)
    assert setup[2].calls == []
    assert read(setup) == first


def test_same_project_distinct_group_coordinates_and_cross_profile_shared_project(setup):
    root, profile, client = setup
    profile["groups"]["admin"] = {
        **profile["groups"]["core"], "config_files": ["admin.yml"],
        "runtime_services": ["admin"], "oneshot_images": {},
    }
    client.records["admin"] = [client.container(C)]
    result = adopt(setup)
    assert set(result["images"]) == {"api", "admin", "migrate"}
    profile["service"] = "another"
    assert release.adopt_release(profile, root=root, client=client)["service"] == "another"


def test_duplicate_service_between_groups_rejected_before_docker(setup):
    setup[1]["groups"]["other"] = copy.deepcopy(setup[1]["groups"]["core"])
    with pytest.raises(release.ServiceReleaseError, match="중복"):
        adopt(setup)
    assert setup[2].calls == []


@pytest.mark.parametrize("overlay", [
    "services: {}\n",
    "services:\n  api:\n    image: " + A + "\n",
    "services:\n  api:\n    image: " + A + "\n    command: evil\n  migrate:\n    image: " + B + "\n",
    "services:\n  api:\n    image: " + A + "\n    image: " + A + "\n  migrate:\n    image: " + B + "\n",
])
def test_incomplete_extra_or_duplicate_overlay_rejected(setup, overlay):
    leaf = Path(adopt(setup)["release_directory"])
    (leaf / "images.yml").write_text(overlay)
    with pytest.raises(release.ServiceReleaseError):
        read(setup)


@pytest.mark.parametrize("target", ["relative", "outside", "indirect"])
def test_pointer_must_point_to_absolute_direct_safe_leaf(setup, tmp_path, target):
    first = adopt(setup)
    pointer = setup[0] / "current" / "sample"
    pointer.unlink()
    if target == "relative":
        destination = Path(first["release_directory"]).name
    elif target == "outside":
        destination = str(tmp_path)
    else:
        indirect = setup[0] / "indirect"
        indirect.symlink_to(first["release_directory"])
        destination = str(indirect)
    pointer.symlink_to(destination)
    with pytest.raises(release.ServiceReleaseError):
        read(setup)


@pytest.mark.parametrize("damage", ["missing", "symlink", "hardlink", "writable", "unknown-schema", "oversize"])
def test_unsafe_or_incomplete_release_files_rejected(setup, tmp_path, damage):
    leaf = Path(adopt(setup)["release_directory"])
    file = leaf / "manifest.json"
    if damage in {"missing", "symlink", "hardlink"}:
        backup = tmp_path / "backup.json"
        backup.write_bytes(file.read_bytes())
        file.unlink()
        if damage == "symlink":
            file.symlink_to(backup)
        elif damage == "hardlink":
            os.link(backup, file)
    elif damage == "writable":
        file.chmod(0o666)
    elif damage == "oversize":
        file.write_text("x" * (release._MAX_BYTES + 1))
    else:
        value = json.loads(file.read_text())
        value["schema"] = "unknown"
        file.write_text(json.dumps(value))
    with pytest.raises(release.ServiceReleaseError):
        read(setup)


@pytest.mark.parametrize("failure", ["manifest-write", "pointer-replace"])
def test_atomic_failure_keeps_previous_current(setup, monkeypatch, failure):
    first = adopt(setup)
    setup[2].records["api"][0]["Image"] = C

    def fail(*args, **kwargs):
        raise OSError("injected")

    if failure == "manifest-write":
        monkeypatch.setattr(release, "atomic_write_json", fail)
    else:
        original = release.os.replace

        def replace(source, target):
            if Path(target).name == "sample":
                raise OSError("injected")
            return original(source, target)

        monkeypatch.setattr(release.os, "replace", replace)
    with pytest.raises(release.ServiceReleaseError):
        adopt(setup, replace_current=True)
    assert read(setup) == first
    assert not list((setup[0] / "current").glob(".*.tmp"))


def test_lock_contention_prevents_all_docker_and_state_writes(setup, monkeypatch):
    @contextmanager
    def busy():
        raise RuntimeError("G busy")
        yield

    monkeypatch.setattr(release, "manager_mutation_lock", busy)
    with pytest.raises(RuntimeError, match="G busy"):
        adopt(setup)
    assert setup[2].calls == []
    assert list(setup[0].iterdir()) == []


@pytest.mark.parametrize("field", ["Id", "Image", "StartedAt", "Health"])
def test_runtime_changes_between_snapshots_preserve_pointer(setup, field):
    first = adopt(setup)
    client = setup[2]
    client.records["api"][0]["Image"] = C
    observations = 0

    def change(docker):
        nonlocal observations
        observations += 1
        if observations != 2:
            return
        container = docker.records["api"][0]
        if field == "Id":
            container[field] = "replacement-container"
        elif field == "Image":
            container[field] = A
        elif field == "StartedAt":
            container["State"][field] = "2026-10-28T01:00:00Z"
        else:
            container["State"]["Health"]["Status"] = "starting"

    client.on_list = change
    with pytest.raises(release.ServiceReleaseError, match="관측 도중"):
        adopt(setup, replace_current=True)
    assert read(setup) == first


@pytest.mark.parametrize("directory", ["root", "current", "leaf"])
def test_group_writable_state_directory_rejected(setup, directory):
    first = adopt(setup)
    target = {"root": setup[0], "current": setup[0] / "current",
              "leaf": Path(first["release_directory"])}[directory]
    target.chmod(0o775)
    with pytest.raises(release.ServiceReleaseError, match="권한"):
        read(setup)


def test_wrong_owner_and_symlink_in_root_path_rejected(setup, monkeypatch):
    first = adopt(setup)
    monkeypatch.setattr(release, "_OWNER_UID", os.getuid() + 1)
    with pytest.raises(release.ServiceReleaseError):
        read(setup)
    monkeypatch.setattr(release, "_OWNER_UID", os.getuid())
    alias = setup[0].parent / "alias"
    alias.symlink_to(setup[0], target_is_directory=True)
    with pytest.raises(release.ServiceReleaseError):
        release.read_current_release("sample", root=alias)
    assert read(setup) == first


def test_reader_fixes_pointer_target_once_during_concurrent_pointer_replace(setup, monkeypatch):
    first = adopt(setup)
    setup[2].records["api"][0]["Image"] = C
    second = adopt(setup, replace_current=True)
    pointer = setup[0] / "current" / "sample"
    pointer.unlink()
    pointer.symlink_to(first["release_directory"])
    original = release.os.readlink
    reads = 0

    def changing(path, *args, **kwargs):
        nonlocal reads
        result = original(path, *args, **kwargs)
        if Path(path) == pointer:
            reads += 1
            pointer.unlink()
            pointer.symlink_to(second["release_directory"])
        return result

    monkeypatch.setattr(release.os, "readlink", changing)
    assert read(setup) == first
    assert reads == 1
    monkeypatch.setattr(release.os, "readlink", original)
    assert read(setup) == second


def test_verify_reports_health_failure_without_hiding_observed_image(setup):
    adopt(setup)
    setup[2].records["api"][0]["State"]["Health"]["Status"] = "unhealthy"
    report = verify(setup)
    assert report["result"] == "drift"
    assert report["images"]["api"] == {"expected": A, "actual": A}


def test_explicit_replace_can_reregister_oneshot_image_only(setup):
    first = adopt(setup)
    setup[1]["groups"]["core"]["oneshot_images"]["migrate"] = C
    with pytest.raises(release.ServiceReleaseError, match="replace_current"):
        adopt(setup)
    second = adopt(setup, replace_current=True)
    assert second["images"]["migrate"]["id"] == C
    assert second["previous"] == first["release_directory"]


@pytest.mark.parametrize("damage", ["owner", "writable", "symlink"])
def test_every_root_ancestor_requires_root_owned_nonwritable_real_directory(monkeypatch, damage):
    root = Path("/srv/releases/public")
    visited = []
    unsafe = Path("/srv")

    def metadata(path):
        visited.append(path)
        mode = stat.S_IFDIR | 0o755
        owner = 0
        if path == unsafe:
            if damage == "owner":
                owner = 1000
            elif damage == "writable":
                mode |= 0o020
            else:
                mode = stat.S_IFLNK | 0o777
        return SimpleNamespace(st_mode=mode, st_uid=owner)

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    with pytest.raises(release.ServiceReleaseError):
        release._root(root)
    assert unsafe in visited


def test_compose_lexical_coordinates_preserve_symlink_project_root(setup, tmp_path):
    actual = tmp_path / "nvme"
    actual.mkdir()
    lexical = tmp_path / "apps"
    lexical.symlink_to(actual, target_is_directory=True)
    setup[1]["groups"]["core"]["working_dir"] = str(lexical)
    setup[1]["groups"]["core"]["env_file"] = ".env"
    result = adopt(setup)
    group = result["groups"]["core"]
    assert group["working_dir"] == str(lexical)
    assert group["config_files"] == [str(lexical / "compose.yml")]
    assert group["env_file"] == str(lexical / ".env")
    assert read(setup) == result


def test_docker_failure_reports_type_without_sensitive_exception_text(setup):
    def failing(**kwargs):
        raise ConnectionError("SECRET=docker-connection-details")

    setup[2].containers.list = failing
    with pytest.raises(release.ServiceReleaseError) as caught:
        adopt(setup)
    assert "runtime 조회 실패 (ConnectionError)" in str(caught.value)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("filename", ["manifest.json", "images.yml"])
def test_fifo_state_file_is_rejected_without_waiting_for_writer(setup, filename):
    first = adopt(setup)
    pointer = setup[0] / "current" / "sample"
    original_target = pointer.readlink()
    file = Path(first["release_directory"]) / filename
    file.unlink()
    os.mkfifo(file, mode=0o644)
    script = """
import os
import sys
from pathlib import Path
from kor_travel_docker_manager.services import service_release as release

release._OWNER_UID = os.getuid()
release._trusted_ancestors = release._directory
try:
    release.read_current_release("sample", root=Path(sys.argv[1]))
except release.ServiceReleaseError:
    raise SystemExit(0)
raise SystemExit("FIFO was accepted")
"""
    # 수정 전 코드는 writer가 없는 FIFO open에서 멈춘다. 자식만 종료할 수 있게 격리한다.
    completed = subprocess.run(
        [sys.executable, "-c", script, str(setup[0])],
        capture_output=True, text=True, timeout=3, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert pointer.readlink() == original_target


def test_pinned_manifest_verification_does_not_reread_pointer(setup, monkeypatch):
    manifest = adopt(setup)
    client = setup[2]
    client.containers.list = client.list
    def unexpected(*args, **kwargs):
        raise AssertionError("pinned wrapper must not reread current")
    monkeypatch.setattr(release, "read_current_release", unexpected)
    assert release.verify_release_manifest(manifest, client=client)["result"] == "healthy"
    client.records["api"][0]["Image"] = C
    drift = release.verify_release_manifest(manifest, client=client)
    assert drift["result"] == "drift"
    assert drift["images"]["api"] == {"expected": A, "actual": C}
