"""`ktdctl pinvi-pair capture`의 wire format·비파괴성·거부 경로 검증.

산출물의 소비자는 Map 저장소의 C7 runner(`scripts/lib/c7_prod_attestation.py`)다.
아래 `RUNNER_*` 상수와 `runner_*` 함수는 그 모듈의 술어를 행 번호 주석과 함께 그대로
옮긴 사본이며, runner가 계약을 바꾸면 이 파일이 먼저 red가 되어야 한다.
"""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from kor_travel_docker_manager.services import c6c_deployment
from kor_travel_docker_manager.services import c6c_pair_capture as capture

# ---------------------------------------------------------------------------
# cross-repo 계약 고정: c7_prod_attestation.py 사본
# ---------------------------------------------------------------------------

RUNNER_IMAGE_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")  # 21행 IMAGE_PATTERN
RUNNER_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")  # 22행 COMMIT_PATTERN
RUNNER_GENERATION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")  # 23행
# 30-36행 PAIR_RUNTIME_IMAGE_FIELDS
RUNNER_PAIR_RUNTIME_IMAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("map_api", "map_image_id"),
    ("map_ui", "map_ui_image_id"),
    ("map_dagster_web", "map_dagster_image_id"),
    ("map_dagster_daemon", "map_dagster_daemon_image_id"),
    ("pinvi_api", "pinvi_image_id"),
)
RUNNER_MANIFEST_TOP_KEYS = frozenset({"active", "rollback", "version"})  # 428행
RUNNER_PAIR_KEYS = frozenset(
    {field_name for _role, field_name in RUNNER_PAIR_RUNTIME_IMAGE_FIELDS}
) | frozenset(
    {  # 305-316행 _validate_pair
        "contract_generation",
        "map_source_revision",
        "pinvi_source_revision",
        "recorded_at",
    }
)


class RunnerAttestationError(RuntimeError):
    """runner `AttestationError` 대응."""


def runner_exact_dict(value: object, keys: set[str]) -> bool:
    """runner 65-66행 `_exact_dict`."""

    return isinstance(value, dict) and set(value) == keys


def runner_validate_pair(value: object) -> None:
    """runner 305-341행 `_validate_pair`를 그대로 옮긴 것."""

    if not runner_exact_dict(value, set(RUNNER_PAIR_KEYS)):
        raise RunnerAttestationError("pair shape")
    assert isinstance(value, dict)
    for _role, field_name in RUNNER_PAIR_RUNTIME_IMAGE_FIELDS:
        image_id = value[field_name]
        if not isinstance(image_id, str) or RUNNER_IMAGE_PATTERN.fullmatch(image_id) is None:
            raise RunnerAttestationError("pair image")
    for revision_field in ("map_source_revision", "pinvi_source_revision"):
        revision = value[revision_field]
        if not isinstance(revision, str) or RUNNER_COMMIT_PATTERN.fullmatch(revision) is None:
            raise RunnerAttestationError(revision_field)
    generation = value["contract_generation"]
    if not isinstance(generation, str) or RUNNER_GENERATION_PATTERN.fullmatch(generation) is None:
        raise RunnerAttestationError("generation")
    recorded_at = value["recorded_at"]
    if not isinstance(recorded_at, str):
        raise RunnerAttestationError("recorded_at")
    observed_at = datetime.fromisoformat(recorded_at)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise RunnerAttestationError("recorded_at")


def runner_validate_manifest_bytes(manifest_bytes: bytes) -> dict[str, Any]:
    """runner 357-361행 + 427-432행."""

    manifest = json.loads(manifest_bytes)
    if not runner_exact_dict(manifest, set(RUNNER_MANIFEST_TOP_KEYS)) or manifest["version"] != 4:
        raise RunnerAttestationError("manifest shape")
    assert isinstance(manifest, dict)
    runner_validate_pair(manifest["active"])
    runner_validate_pair(manifest["rollback"])
    return manifest


def runner_read_secure_file(
    path: Path,
    mode: int,
    *,
    expected_uid: int,
    expected_gid: int,
    ancestor_floor: Path,
) -> bytes:
    """runner 112-164행 `_read_secure_file`을 그대로 옮긴 것."""

    if not path.is_absolute():
        raise RunnerAttestationError("root file path is not absolute")
    floor = ancestor_floor.resolve(strict=True)
    try:
        path.relative_to(floor)
    except ValueError as exc:
        raise RunnerAttestationError("file is outside trusted ancestor floor") from exc
    reached_floor = False
    for parent in path.parents:
        observed_parent = parent.lstat()
        if (
            not stat.S_ISDIR(observed_parent.st_mode)
            or parent.is_symlink()
            or observed_parent.st_uid != expected_uid
            or observed_parent.st_gid != expected_gid
            or stat.S_IMODE(observed_parent.st_mode) & 0o022
        ):
            raise RunnerAttestationError("unsafe root file parent")
        if parent == floor:
            reached_floor = True
            break
    if not reached_floor:
        raise RunnerAttestationError("trusted ancestor floor was not reached")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != expected_uid
            or observed.st_gid != expected_gid
            or stat.S_IMODE(observed.st_mode) != mode
        ):
            raise RunnerAttestationError("unsafe root-owned file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


# ---------------------------------------------------------------------------
# fixture 값
# ---------------------------------------------------------------------------

MAP_REVISION = "a1" * 20
PINVI_REVISION = "b2" * 20
OTHER_REVISION = "c3" * 20
GENERATION = "c6c-ops-v1"
PROJECT = "kortravel"


def _image(seed: int) -> str:
    return "sha256:" + f"{seed:x}".rjust(64, "0")


def _container(seed: int) -> str:
    return f"{seed:x}".rjust(64, "0")


ROLE_IMAGES = {
    role: _image(0x100 + index)
    for index, (role, _service, _field) in enumerate(capture.CAPTURE_ROLES)
}
ROLE_CONTAINERS = {
    role: _container(0x200 + index)
    for index, (role, _service, _field) in enumerate(capture.CAPTURE_ROLES)
}
SERVICE_BY_ROLE = {role: service for role, service, _field in capture.CAPTURE_ROLES}


@dataclass
class FakeDockerGit:
    """읽기 전용 docker/git 조회만 응답하는 fake runner."""

    containers: dict[str, str] = field(default_factory=lambda: dict(ROLE_CONTAINERS))
    images: dict[str, str] = field(default_factory=lambda: dict(ROLE_IMAGES))
    project: dict[str, str] = field(
        default_factory=lambda: {role: PROJECT for role in ROLE_CONTAINERS}
    )
    service_label: dict[str, str] = field(default_factory=lambda: dict(SERVICE_BY_ROLE))
    state: dict[str, dict[str, Any]] = field(default_factory=dict)
    revisions: dict[str, str] = field(default_factory=dict)
    build_environment: str = "production"
    ps_stdout: dict[str, str] | None = None
    ps_returncode: int = 0
    local_images: set[str] | None = None
    git_cat_file_returncode: int = 0
    git_status_stdout: str = ""
    argv: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for role in ROLE_CONTAINERS:
            self.state.setdefault(
                role,
                {
                    "Running": True,
                    "Paused": False,
                    "Restarting": False,
                    "Health": {"Status": "healthy"},
                },
            )
            self.revisions.setdefault(
                role, PINVI_REVISION if role == "pinvi_api" else MAP_REVISION
            )

    def _role_for_service(self, service: str) -> str:
        return next(role for role, name, _f in capture.CAPTURE_ROLES if name == service)

    def _role_for_container(self, container_id: str) -> str:
        return next(role for role, value in self.containers.items() if value == container_id)

    def _role_for_image(self, image_id: str) -> str | None:
        for role, value in self.images.items():
            if value == image_id:
                return role
        return None

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.argv.append(list(argv))
        if argv[:2] == ["docker", "compose"]:
            role = self._role_for_service(argv[6])
            if self.ps_stdout is not None:
                stdout = self.ps_stdout.get(role, "")
            else:
                stdout = self.containers[role] + "\n"
            return self._completed(self.ps_returncode, stdout)
        if argv[:2] == ["docker", "inspect"]:
            role = self._role_for_container(argv[3])
            record = {
                "Id": self.containers[role],
                "Image": self.images[role],
                "State": self.state[role],
                "Config": {
                    "Labels": {
                        "com.docker.compose.service": self.service_label[role],
                        "com.docker.compose.project": self.project[role],
                    }
                },
            }
            return self._completed(0, json.dumps([record]))
        if argv[:3] == ["docker", "image", "inspect"]:
            image_id = argv[5]
            if self.local_images is not None and image_id not in self.local_images:
                return self._completed(1, "")
            if argv[3] == "--format={{.Id}}":
                return self._completed(0, image_id + "\n")
            image_role = self._role_for_image(image_id)
            labels: dict[str, str] = {}
            if image_role is not None:
                labels["org.opencontainers.image.revision"] = self.revisions[image_role]
                if image_role == "pinvi_api":
                    labels["io.pinvi.build.environment"] = self.build_environment
            return self._completed(0, json.dumps(labels))
        if argv[:2] == ["git", "--no-optional-locks"]:
            if "cat-file" in argv:
                return self._completed(self.git_cat_file_returncode, "")
            return self._completed(0, self.git_status_stdout)
        raise AssertionError(f"unexpected argv: {list(argv)}")

    @staticmethod
    def _completed(returncode: int, stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@dataclass(frozen=True)
class _LockSnapshot:
    lock_path: str


@dataclass
class FakeLock:
    lock_path: str = "/run/ktdm/global-mutation.lock"
    entered: int = 0

    def __call__(self) -> Any:
        @contextmanager
        def _cm() -> Iterator[_LockSnapshot]:
            self.entered += 1
            yield _LockSnapshot(lock_path=self.lock_path)

        return _cm()


@dataclass(frozen=True)
class Bench:
    manifest: Path
    floor: Path
    map_checkout: Path
    pinvi_checkout: Path
    environment: dict[str, str]
    project_directory: str


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bench:
    base = tmp_path.resolve()
    floor = base / "state"
    floor.mkdir(mode=0o700)
    project_dir = floor / PROJECT
    project_dir.mkdir(mode=0o700)
    map_checkout = base / "map-checkout"
    map_checkout.mkdir(mode=0o700)
    pinvi_checkout = base / "pinvi-checkout"
    pinvi_checkout.mkdir(mode=0o700)
    monkeypatch.setattr(capture, "REQUIRED_EUID", os.geteuid())
    monkeypatch.setattr(capture, "RUNNER_FILE_UID", os.geteuid())
    monkeypatch.setattr(capture, "RUNNER_FILE_GID", os.getgid())
    monkeypatch.setattr(capture, "RUNNER_ANCESTOR_FLOOR", floor)
    return Bench(
        manifest=project_dir / c6c_deployment.PAIR_MANIFEST_FILENAME,
        floor=floor,
        map_checkout=map_checkout,
        pinvi_checkout=pinvi_checkout,
        environment={
            "KTDM_C6C_CONTRACT_GENERATION": GENERATION,
            "COMPOSE_PROJECT_NAME": PROJECT,
        },
        project_directory="/srv/kor-travel",
    )


def run_capture(
    bench: Bench,
    runner: FakeDockerGit | None = None,
    lock: Callable[[], Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "verified_compatible": True,
        "manifest_path": str(bench.manifest),
        "map_source_checkout": str(bench.map_checkout),
        "pinvi_source_checkout": str(bench.pinvi_checkout),
        "project_directory": bench.project_directory,
        "environment": bench.environment,
        "runner": runner if runner is not None else FakeDockerGit(),
        "lock": lock if lock is not None else FakeLock(),
    }
    arguments.update(overrides)
    return capture.capture_compatible_pair(**arguments)


def seed_manifest(bench: Bench, *, active_map_image: str, mode: int = 0o600) -> bytes:
    """기존 정규 v4 manifest를 심는다."""

    pair = c6c_deployment.new_image_pair(
        active_map_image,
        _image(0x999),
        GENERATION,
        map_ui_image_id=_image(0x901),
        map_dagster_image_id=_image(0x902),
        map_dagster_daemon_image_id=_image(0x903),
        map_source_revision=OTHER_REVISION,
        pinvi_source_revision=OTHER_REVISION,
    )
    payload = c6c_deployment.pair_manifest_bytes(c6c_deployment.initial_pair_manifest(pair))
    bench.manifest.write_bytes(payload)
    bench.manifest.chmod(mode)
    return payload


# ---------------------------------------------------------------------------
# wire format 골든
# ---------------------------------------------------------------------------


def test_committed_manifest_passes_the_c7_runner_predicates(bench: Bench) -> None:
    receipt = run_capture(bench)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["returncode"] == 0
    committed = runner_read_secure_file(
        bench.manifest,
        0o600,
        expected_uid=os.geteuid(),
        expected_gid=os.getgid(),
        ancestor_floor=bench.floor,
    )
    manifest = runner_validate_manifest_bytes(committed)
    assert isinstance(manifest["version"], int) and not isinstance(manifest["version"], bool)
    active = manifest["active"]
    assert active["contract_generation"] == GENERATION
    assert active["map_source_revision"] == MAP_REVISION
    assert active["pinvi_source_revision"] == PINVI_REVISION
    for role, field_name in RUNNER_PAIR_RUNTIME_IMAGE_FIELDS:
        assert active[field_name] == ROLE_IMAGES[role]


def test_manifest_key_sets_match_the_runner_contract(bench: Bench) -> None:
    run_capture(bench)
    manifest = json.loads(bench.manifest.read_bytes())

    assert set(manifest) == set(RUNNER_MANIFEST_TOP_KEYS)
    assert set(manifest["active"]) == set(RUNNER_PAIR_KEYS)
    assert set(manifest["rollback"]) == set(RUNNER_PAIR_KEYS)
    assert set(c6c_deployment.PAIR_MANIFEST_TOP_KEYS) == set(RUNNER_MANIFEST_TOP_KEYS)
    assert set(c6c_deployment.PAIR_MANIFEST_PAIR_KEYS) == set(RUNNER_PAIR_KEYS)
    assert tuple(role for role, _service, _f in capture.CAPTURE_ROLES) == tuple(
        role for role, _f in RUNNER_PAIR_RUNTIME_IMAGE_FIELDS
    )
    assert tuple(field_name for _r, _s, field_name in capture.CAPTURE_ROLES) == tuple(
        field_name for _r, field_name in RUNNER_PAIR_RUNTIME_IMAGE_FIELDS
    )


def test_serialization_is_sorted_two_space_json_with_trailing_newline(bench: Bench) -> None:
    run_capture(bench)
    raw = bench.manifest.read_bytes()

    assert raw.endswith(b"\n")
    assert raw.decode("utf-8") == (
        json.dumps(json.loads(raw), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


@pytest.mark.skipif(
    not Path("F:/dev/ktm-tvn36r/scripts/lib/c7_prod_attestation.py").exists()
    and not Path("/mnt/f/dev/ktm-tvn36r/scripts/lib/c7_prod_attestation.py").exists(),
    reason="Map 저장소 C7 runner 체크아웃이 없으면 사본 술어만으로 검증한다",
)
def test_real_runner_module_accepts_the_committed_pair(bench: Bench) -> None:
    import importlib.util

    module_path = next(
        candidate
        for candidate in (
            Path("/mnt/f/dev/ktm-tvn36r/scripts/lib/c7_prod_attestation.py"),
            Path("F:/dev/ktm-tvn36r/scripts/lib/c7_prod_attestation.py"),
        )
        if candidate.exists()
    )
    spec = importlib.util.spec_from_file_location("c7_prod_attestation", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    run_capture(bench)
    manifest = json.loads(bench.manifest.read_bytes())
    module._validate_pair(manifest["active"])
    module._validate_pair(manifest["rollback"])
    assert module._exact_dict(manifest, {"active", "rollback", "version"})
    assert manifest["version"] == 4


# ---------------------------------------------------------------------------
# rollback 승격 의미
# ---------------------------------------------------------------------------


def test_first_capture_duplicates_active_into_rollback(bench: Bench) -> None:
    run_capture(bench)
    manifest = json.loads(bench.manifest.read_bytes())

    assert manifest["rollback"] == manifest["active"]


def test_recapture_promotes_the_previous_active_to_rollback(bench: Bench) -> None:
    previous = json.loads(seed_manifest(bench, active_map_image=_image(0x900)))

    run_capture(bench)

    manifest = json.loads(bench.manifest.read_bytes())
    assert manifest["rollback"] == previous["active"]
    assert manifest["active"]["map_image_id"] == ROLE_IMAGES["map_api"]


def test_recapture_with_the_same_identity_preserves_the_existing_rollback(bench: Bench) -> None:
    seed_manifest(bench, active_map_image=_image(0x900))
    run_capture(bench)
    promoted = json.loads(bench.manifest.read_bytes())

    run_capture(bench)

    manifest = json.loads(bench.manifest.read_bytes())
    assert manifest["rollback"] == promoted["rollback"]
    assert manifest["active"]["map_image_id"] == promoted["active"]["map_image_id"]


# ---------------------------------------------------------------------------
# C-6 기존 파일 사전 검증
# ---------------------------------------------------------------------------


def _foreign_document(kind: str) -> bytes:
    pair = {
        "map_image_id": _image(1),
        "map_ui_image_id": _image(2),
        "map_dagster_image_id": _image(3),
        "map_dagster_daemon_image_id": _image(4),
        "pinvi_image_id": _image(5),
        "map_source_revision": OTHER_REVISION,
        "pinvi_source_revision": OTHER_REVISION,
        "contract_generation": GENERATION,
        "recorded_at": "2026-08-18T00:00:00+00:00",
    }
    if kind == "version_3":
        payload: dict[str, Any] = {"version": 3, "active": pair, "rollback": pair}
    elif kind == "extra_key":
        payload = {"version": 4, "active": pair, "rollback": pair, "note": "extra"}
    elif kind == "missing_key":
        payload = {"version": 4, "active": pair}
    elif kind == "pair_extra_field":
        payload = {"version": 4, "active": {**pair, "extra": "x"}, "rollback": pair}
    elif kind == "pair_missing_field":
        reduced = dict(pair)
        reduced.pop("recorded_at")
        payload = {"version": 4, "active": reduced, "rollback": pair}
    elif kind == "bool_version":
        payload = {"version": True, "active": pair, "rollback": pair}
    else:  # pragma: no cover - 방어
        raise AssertionError(kind)
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


@pytest.mark.parametrize(
    "kind",
    [
        "version_3",
        "extra_key",
        "missing_key",
        "pair_extra_field",
        "pair_missing_field",
        "bool_version",
    ],
)
def test_foreign_manifest_document_is_refused_without_touching_it(bench: Bench, kind: str) -> None:
    original = _foreign_document(kind)
    bench.manifest.write_bytes(original)
    bench.manifest.chmod(0o600)
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert excinfo.value.returncode == 2
    assert bench.manifest.read_bytes() == original
    assert runner.argv == []


def test_broken_json_manifest_is_refused(bench: Bench) -> None:
    bench.manifest.write_bytes(b"{not json")
    bench.manifest.chmod(0o600)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert bench.manifest.read_bytes() == b"{not json"


def test_group_readable_existing_manifest_is_refused(bench: Bench) -> None:
    original = seed_manifest(bench, active_map_image=_image(0x900), mode=0o644)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert bench.manifest.read_bytes() == original
    assert stat.S_IMODE(bench.manifest.stat().st_mode) == 0o644


@pytest.mark.parametrize("attribute", ["RUNNER_FILE_UID", "RUNNER_FILE_GID"])
def test_foreign_owned_existing_manifest_is_refused(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    """C-6 단독 검증. 소유자 불일치는 parent 검사와 무관하게 파일 자체에서 거부된다."""

    original = seed_manifest(bench, active_map_image=_image(0x900))
    monkeypatch.setattr(capture, attribute, getattr(capture, attribute) + 4242)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        capture._existing_manifest(bench.manifest)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert "owner" in excinfo.value.reason
    assert bench.manifest.read_bytes() == original


def test_symlinked_manifest_is_refused(bench: Bench) -> None:
    target = bench.manifest.parent / "elsewhere.json"
    target.write_bytes(b"{}")
    target.chmod(0o600)
    bench.manifest.symlink_to(target)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert bench.manifest.is_symlink()
    assert target.read_bytes() == b"{}"


def test_hardlinked_manifest_is_refused(bench: Bench) -> None:
    original = seed_manifest(bench, active_map_image=_image(0x900))
    os.link(bench.manifest, bench.manifest.parent / "second-name.json")

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert bench.manifest.read_bytes() == original


def test_directory_at_the_manifest_path_is_refused(bench: Bench) -> None:
    bench.manifest.mkdir(mode=0o700)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert bench.manifest.is_dir()


# ---------------------------------------------------------------------------
# C-4 parent 정책 (capture는 절대 mkdir하지 않는다)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent_mode", [0o770, 0o707])
def test_writable_ancestor_is_refused_without_creating_anything(
    bench: Bench, parent_mode: int
) -> None:
    bench.manifest.parent.chmod(parent_mode)
    runner = FakeDockerGit()

    try:
        with pytest.raises(capture.PairCaptureRefusal) as excinfo:
            run_capture(bench, runner=runner)
    finally:
        bench.manifest.parent.chmod(0o700)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert not bench.manifest.exists()
    assert runner.argv == []


def test_missing_parent_is_refused_and_not_created(bench: Bench) -> None:
    missing = bench.manifest.parent / "deeper" / c6c_deployment.PAIR_MANIFEST_FILENAME
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, manifest_path=str(missing))

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert not missing.parent.exists()
    assert runner.argv == []


def test_symlinked_ancestor_is_refused(bench: Bench) -> None:
    real = bench.floor / "real-project"
    real.mkdir(mode=0o700)
    linked = bench.floor / "linked-project"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(
            bench,
            manifest_path=str(linked / c6c_deployment.PAIR_MANIFEST_FILENAME),
        )

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert not (real / c6c_deployment.PAIR_MANIFEST_FILENAME).exists()


def test_foreign_owned_ancestor_is_refused(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "RUNNER_FILE_UID", os.geteuid() + 4242)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert not bench.manifest.exists()


# ---------------------------------------------------------------------------
# C-1~C-3, C-13 precondition
# ---------------------------------------------------------------------------


def test_missing_verified_compatible_calls_nothing(bench: Bench) -> None:
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, verified_compatible=False)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert excinfo.value.returncode == 2
    assert "--verified-compatible" in str(excinfo.value)
    assert runner.argv == []
    assert not bench.manifest.exists()


@pytest.mark.parametrize("option", ["manifest_path", "map_source_checkout", "pinvi_source_checkout"])
def test_missing_required_path_is_refused(bench: Bench, option: str) -> None:
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, **{option: None})

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert runner.argv == []


@pytest.mark.parametrize(
    "bad_path",
    ["relative/compatible-pair-v4.json", "/srv/../srv/compatible-pair-v4.json"],
)
def test_non_canonical_manifest_path_is_refused(bench: Bench, bad_path: str) -> None:
    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, manifest_path=bad_path)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION


def test_wrong_manifest_basename_is_refused(bench: Bench) -> None:
    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, manifest_path=str(bench.manifest.parent / "pair.json"))

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert c6c_deployment.PAIR_MANIFEST_FILENAME in str(excinfo.value)


def test_non_root_execution_is_refused(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "REQUIRED_EUID", os.geteuid() + 4242)
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert runner.argv == []


@pytest.mark.parametrize(
    "environment",
    [
        {"COMPOSE_PROJECT_NAME": PROJECT},
        {"KTDM_C6C_CONTRACT_GENERATION": "!bad!", "COMPOSE_PROJECT_NAME": PROJECT},
        {"KTDM_C6C_CONTRACT_GENERATION": GENERATION},
        {"KTDM_C6C_CONTRACT_GENERATION": GENERATION, "COMPOSE_PROJECT_NAME": "x"},
    ],
)
def test_incomplete_environment_contract_is_refused(
    bench: Bench, environment: dict[str, str]
) -> None:
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, environment=environment)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert runner.argv == []


def test_lock_contention_maps_to_its_own_terminal_state(bench: Bench) -> None:
    def contended() -> Any:
        raise c6c_deployment.DeploymentContractError(capture.LOCK_CONTENTION_MESSAGE)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, lock=contended)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_LOCK_CONTENDED
    assert excinfo.value.returncode == 2


def test_deployment_lock_emits_exactly_the_message_capture_matches(tmp_path: Path) -> None:
    """cross-module 결박: capture의 contended 분기는 이 문자열 하나에 걸려 있다."""

    lock_path = str(tmp_path.resolve() / "global-mutation.lock")
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        # 같은 프로세스라도 별도 open file description이면 flock은 실제로 경합한다.
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(c6c_deployment.DeploymentContractError) as excinfo:
            with c6c_deployment.c6c_deployment_lock(lock_path):
                pass  # pragma: no cover - 도달하면 안 된다
    finally:
        os.close(holder)

    assert str(excinfo.value) == capture.LOCK_CONTENTION_MESSAGE


# ---------------------------------------------------------------------------
# C-7~C-16 runtime 관측
# ---------------------------------------------------------------------------


def _expect_runtime_refusal(bench: Bench, runner: FakeDockerGit) -> capture.PairCaptureRefusal:
    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_RUNTIME
    assert excinfo.value.returncode == 1
    assert not bench.manifest.exists()
    return excinfo.value


@pytest.mark.parametrize("stdout", ["", "\n".join([_container(0x201), _container(0x202)]) + "\n"])
def test_compose_service_cardinality_is_enforced(bench: Bench, stdout: str) -> None:
    runner = FakeDockerGit(ps_stdout={role: stdout for role in ROLE_CONTAINERS})
    _expect_runtime_refusal(bench, runner)


def test_non_hex_container_id_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit(ps_stdout={role: "not-a-container\n" for role in ROLE_CONTAINERS})
    _expect_runtime_refusal(bench, runner)


@pytest.mark.parametrize(
    "state",
    [
        {"Running": False, "Paused": False, "Restarting": False, "Health": {"Status": "healthy"}},
        {"Running": True, "Paused": True, "Restarting": False, "Health": {"Status": "healthy"}},
        {"Running": True, "Paused": False, "Restarting": True, "Health": {"Status": "healthy"}},
        {"Running": True, "Paused": False, "Restarting": False, "Health": {"Status": "starting"}},
    ],
)
def test_unhealthy_container_is_refused(bench: Bench, state: dict[str, Any]) -> None:
    runner = FakeDockerGit()
    runner.state["map_api"] = state
    _expect_runtime_refusal(bench, runner)


def test_duplicate_container_ids_are_refused(bench: Bench) -> None:
    runner = FakeDockerGit()
    runner.containers["map_ui"] = runner.containers["map_api"]
    _expect_runtime_refusal(bench, runner)


def test_two_compose_projects_are_refused(bench: Bench) -> None:
    runner = FakeDockerGit()
    runner.project["pinvi_api"] = "other-project"
    _expect_runtime_refusal(bench, runner)


def test_compose_project_must_match_the_frozen_environment(bench: Bench) -> None:
    runner = FakeDockerGit(project={role: "unexpected" for role in ROLE_CONTAINERS})
    _expect_runtime_refusal(bench, runner)


def test_compose_service_label_mismatch_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit()
    runner.service_label["map_ui"] = "someone-elses-service"
    _expect_runtime_refusal(bench, runner)


def test_map_images_with_divergent_revisions_are_refused(bench: Bench) -> None:
    runner = FakeDockerGit()
    runner.revisions["map_dagster_daemon"] = OTHER_REVISION
    _expect_runtime_refusal(bench, runner)


def test_pinvi_image_without_production_build_environment_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit(build_environment="staging")
    _expect_runtime_refusal(bench, runner)


def test_missing_revision_label_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit()
    runner.revisions["map_api"] = "not-a-commit"
    _expect_runtime_refusal(bench, runner)


def test_image_absent_from_the_local_daemon_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit(local_images=set(ROLE_IMAGES.values()) - {ROLE_IMAGES["map_ui"]})
    _expect_runtime_refusal(bench, runner)


def test_expected_map_revision_mismatch_is_refused(bench: Bench) -> None:
    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, expect_active_map_revision=OTHER_REVISION)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_RUNTIME
    assert not bench.manifest.exists()


def test_expected_map_revision_match_commits(bench: Bench) -> None:
    receipt = run_capture(bench, expect_active_map_revision=MAP_REVISION)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["map_source_revision"] == MAP_REVISION


def test_expected_map_revision_must_be_forty_hex(bench: Bench) -> None:
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, expect_active_map_revision="abc")

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert runner.argv == []


# ---------------------------------------------------------------------------
# C-14/C-15 git 결박
# ---------------------------------------------------------------------------


def test_absent_commit_object_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit(git_cat_file_returncode=1)
    _expect_runtime_refusal(bench, runner)


def test_dirty_checkout_is_refused(bench: Bench) -> None:
    runner = FakeDockerGit(git_status_stdout=" M backend/src/app.py\n")
    _expect_runtime_refusal(bench, runner)


def test_git_commands_do_not_bypass_ownership_policy(bench: Bench) -> None:
    runner = FakeDockerGit()
    run_capture(bench, runner=runner)

    git_argv = [argv for argv in runner.argv if argv[0] == "git"]
    assert git_argv
    for argv in git_argv:
        assert argv[1] == "--no-optional-locks"
        assert "-c" not in argv
        assert not any(item.startswith("safe.directory") for item in argv)
    assert [
        "git",
        "--no-optional-locks",
        "-C",
        str(bench.map_checkout),
        "cat-file",
        "-e",
        f"{MAP_REVISION}^{{commit}}",
    ] in git_argv
    assert [
        "git",
        "--no-optional-locks",
        "-C",
        str(bench.pinvi_checkout),
        "status",
        "--porcelain=v1",
    ] in git_argv


# ---------------------------------------------------------------------------
# C-16 TOCTOU
# ---------------------------------------------------------------------------


class _MutatingRunner(FakeDockerGit):
    """1차 관측 뒤 runtime이 바뀐 상황을 만든다."""

    def __init__(self, *, change: str) -> None:
        super().__init__()
        self._change = change
        self._compose_calls = 0

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["docker", "compose"]:
            self._compose_calls += 1
            if self._compose_calls == len(capture.CAPTURE_ROLES) + 1:
                if self._change == "container":
                    self.containers["map_api"] = _container(0x777)
                else:
                    self.images["map_api"] = _image(0x777)
        return super().__call__(argv)


@pytest.mark.parametrize("change", ["container", "image"])
def test_runtime_change_between_observations_refuses_before_writing(
    bench: Bench, change: str
) -> None:
    runner = _MutatingRunner(change=change)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_RUNTIME
    assert not bench.manifest.exists()


def test_second_observation_happens_before_the_write(bench: Bench) -> None:
    runner = FakeDockerGit()
    run_capture(bench, runner=runner)

    compose_calls = [argv for argv in runner.argv if argv[:2] == ["docker", "compose"]]
    assert len(compose_calls) == 2 * len(capture.CAPTURE_ROLES)


# ---------------------------------------------------------------------------
# C-17 원자성 / C-18 자기 출력 재검증
# ---------------------------------------------------------------------------


def test_replace_failure_leaves_the_previous_bytes_intact(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = seed_manifest(bench, active_map_image=_image(0x900))

    def broken_replace(source: Any, destination: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(c6c_deployment.os, "replace", broken_replace)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_RUNTIME
    assert bench.manifest.read_bytes() == original
    assert stat.S_IMODE(bench.manifest.stat().st_mode) == 0o600


def test_torn_commit_reports_the_indeterminate_state_without_leaking_values(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_fsync(path: Path) -> None:
        raise OSError("directory fsync failed")

    monkeypatch.setattr(c6c_deployment, "_fsync_directory", broken_fsync)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_WRITE_INDETERMINATE
    assert excinfo.value.returncode == 1
    message = str(excinfo.value)
    assert message.endswith(capture.FENCE_NOTICE)
    assert MAP_REVISION not in message
    assert ROLE_IMAGES["map_api"] not in message


def test_reported_sha256_is_the_hash_of_the_bytes_on_disk(bench: Bench) -> None:
    receipt = run_capture(bench)

    assert receipt["manifest_sha256"] == hashlib.sha256(bench.manifest.read_bytes()).hexdigest()


def test_committed_file_is_owner_only_regular_file(bench: Bench) -> None:
    run_capture(bench)
    observed = bench.manifest.lstat()

    assert stat.S_ISREG(observed.st_mode)
    assert stat.S_IMODE(observed.st_mode) == 0o600
    assert observed.st_uid == os.geteuid()
    assert observed.st_nlink == 1


def test_recorded_at_parses_with_plain_fromisoformat(bench: Bench) -> None:
    run_capture(bench)
    manifest = json.loads(bench.manifest.read_bytes())

    for pair in (manifest["active"], manifest["rollback"]):
        observed_at = datetime.fromisoformat(pair["recorded_at"])
        assert observed_at.utcoffset() is not None


# ---------------------------------------------------------------------------
# 비파괴 불변식
# ---------------------------------------------------------------------------

FORBIDDEN_DOCKER_TOKENS = frozenset(
    {"up", "stop", "start", "rm", "build", "restart", "kill", "down", "create", "exec", "run"}
)


def _is_allowed_docker_argv(argv: Sequence[str], project_directory: str) -> bool:
    if argv[:2] == ["docker", "compose"]:
        return (
            len(argv) == 7
            and argv[2] == "--project-directory"
            and argv[3] == project_directory
            and argv[4:6] == ["ps", "-q"]
        )
    if argv[:2] == ["docker", "inspect"]:
        return len(argv) == 4 and argv[2] == "--"
    if argv[:3] == ["docker", "image", "inspect"]:
        return len(argv) == 6 and argv[3].startswith("--format=") and argv[4] == "--"
    return False


def test_every_docker_argv_matches_the_read_only_allowlist(bench: Bench) -> None:
    runner = FakeDockerGit()
    run_capture(bench, runner=runner)

    docker_argv = [argv for argv in runner.argv if argv[0] == "docker"]
    assert docker_argv
    for argv in docker_argv:
        assert _is_allowed_docker_argv(argv, bench.project_directory), argv


def test_no_mutating_docker_token_appears_anywhere(bench: Bench) -> None:
    runner = FakeDockerGit()
    run_capture(bench, runner=runner)

    for argv in runner.argv:
        if argv[0] != "docker":
            continue
        assert not FORBIDDEN_DOCKER_TOKENS & set(argv), argv


def test_compose_argv_mirrors_the_runner_helper(bench: Bench) -> None:
    """runner `_compose_container`(c7_prod_attestation.py 277-302행)와 토큰 단위 동일."""

    runner = FakeDockerGit()
    run_capture(bench, runner=runner)

    for _role, service, _f in capture.CAPTURE_ROLES:
        expected = [
            "docker",
            "compose",
            "--project-directory",
            bench.project_directory,
            "ps",
            "-q",
            service,
        ]
        assert expected in runner.argv
    for argv in runner.argv:
        assert "--env-file" not in argv
        assert "-f" not in argv


# ---------------------------------------------------------------------------
# receipt 계약
# ---------------------------------------------------------------------------


def test_receipt_key_set_is_frozen_and_non_sensitive(bench: Bench) -> None:
    lock = FakeLock()
    receipt = run_capture(bench, lock=lock)

    assert set(receipt) == set(capture.CAPTURE_RECEIPT_KEYS)
    assert receipt["operator_asserted_verified_compatible"] is True
    assert receipt["rollback_images_present"] is True
    assert receipt["not_guaranteed"] == list(capture.NOT_GUARANTEED)
    assert any(lock.lock_path in effect for effect in receipt["side_effects"])
    assert any(str(bench.manifest) in effect for effect in receipt["side_effects"])
    assert receipt["checkout_uid"] == {"map": os.geteuid(), "pinvi": os.geteuid()}
    assert json.loads(json.dumps(receipt))


def test_stdout_block_only_carries_copyable_attestation_values(bench: Bench) -> None:
    receipt = run_capture(bench)
    lines = receipt["stdout"].splitlines()

    assert lines[0] == f"manifest={bench.manifest}"
    assert f"manifest_sha256={receipt['manifest_sha256']}" in lines
    assert f"contract_generation={GENERATION}" in lines
    assert f"compose_project={PROJECT}" in lines
    assert f"compose_project_directory={bench.project_directory}" in lines
    for role, _service, _f in capture.CAPTURE_ROLES:
        assert f"{role}_image_id={ROLE_IMAGES[role]}" in lines


def test_rollback_images_absent_is_reported_not_fatal(bench: Bench) -> None:
    seed_manifest(bench, active_map_image=_image(0x900))
    runner = FakeDockerGit(local_images=set(ROLE_IMAGES.values()))

    receipt = run_capture(bench, runner=runner)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["rollback_images_present"] is False


# ---------------------------------------------------------------------------
# --build no-op
# ---------------------------------------------------------------------------


def test_build_flag_changes_nothing_but_the_receipt_note(bench: Bench) -> None:
    runner_without = FakeDockerGit()
    receipt_without = run_capture(bench, runner=runner_without)
    without = json.loads(bench.manifest.read_bytes())

    bench.manifest.unlink()
    runner_with = FakeDockerGit()
    receipt_with = run_capture(bench, runner=runner_with, build_flag=True)
    with_build = json.loads(bench.manifest.read_bytes())

    for document in (without, with_build):
        for pair in document.values():
            if isinstance(pair, dict):
                pair.pop("recorded_at")
    assert without == with_build
    assert receipt_without["build_flag_accepted_no_op"] is False
    assert receipt_with["build_flag_accepted_no_op"] is True
    assert all("build" not in argv for argv in runner_with.argv)


def test_not_guaranteed_admits_that_build_builds_nothing() -> None:
    assert any("builds nothing" in item for item in capture.NOT_GUARANTEED)
    assert "builds nothing" in capture.BUILD_FLAG_NOTICE


# ---------------------------------------------------------------------------
# 실패 메시지 규약
# ---------------------------------------------------------------------------


def test_every_refusal_message_ends_with_the_fence_notice(bench: Bench) -> None:
    cases: list[tuple[dict[str, Any], FakeDockerGit | None]] = [
        ({"verified_compatible": False}, None),
        ({"manifest_path": None}, None),
        ({"expect_active_map_revision": "zz"}, None),
        ({}, FakeDockerGit(git_cat_file_returncode=1)),
        ({}, FakeDockerGit(build_environment="staging")),
    ]
    for overrides, runner in cases:
        with pytest.raises(capture.PairCaptureRefusal) as excinfo:
            run_capture(bench, runner=runner, **overrides)
        assert str(excinfo.value).endswith(capture.FENCE_NOTICE)
        assert "fence" not in excinfo.value.reason


def test_terminal_states_map_to_the_declared_exit_codes() -> None:
    assert capture.CAPTURE_EXIT_CODES == {
        capture.CAPTURE_COMMITTED: 0,
        capture.CAPTURE_REFUSED_PRECONDITION: 2,
        capture.CAPTURE_REFUSED_LOCK_CONTENDED: 2,
        capture.CAPTURE_REFUSED_RUNTIME: 1,
        capture.CAPTURE_WRITE_INDETERMINATE: 1,
    }


def test_capture_acquires_the_mutation_lock_exactly_once(bench: Bench) -> None:
    lock = FakeLock()
    run_capture(bench, lock=lock)

    assert lock.entered == 1


def test_default_lock_factory_is_the_same_global_mutation_lock_as_rebuild_pinned() -> None:
    from kor_travel_docker_manager.services import compose_service

    assert (
        capture.c6c_deployment_lock_from_environment
        is compose_service.c6c_deployment_lock_from_environment
    )


def test_required_paths_have_no_default_and_no_state_root_fallback() -> None:
    parameters = inspect.signature(capture.capture_compatible_pair).parameters

    for required in (
        "verified_compatible",
        "manifest_path",
        "map_source_checkout",
        "pinvi_source_checkout",
    ):
        assert parameters[required].default is inspect.Parameter.empty
    source = Path(capture.__file__).read_text(encoding="utf-8")
    assert "c6c_state_paths" not in source
