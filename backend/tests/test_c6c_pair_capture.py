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
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import c6c_deployment, pinned_runtime_generation
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
RUNNER_MANIFEST_TOP_KEYS = frozenset({"active", "rollback", "version"})  # 436행
RUNNER_PAIR_KEYS = frozenset(
    {field_name for _role, field_name in RUNNER_PAIR_RUNTIME_IMAGE_FIELDS}
) | frozenset(
    {  # 313-325행 _validate_pair
        "contract_generation",
        "map_source_revision",
        "pinvi_source_revision",
        "recorded_at",
    }
)


class RunnerAttestationError(RuntimeError):
    """runner `AttestationError` 대응."""


def runner_exact_dict(value: object, keys: set[str]) -> bool:
    """runner 68-69행 `_exact_dict`."""

    return isinstance(value, dict) and set(value) == keys


def runner_validate_pair(value: object) -> None:
    """runner 313-347행 `_validate_pair`를 그대로 옮긴 것."""

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
    """runner 364-365행 + 436-440행."""

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
    """runner 111-162행 `_read_secure_file`을 그대로 옮긴 것."""

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
ROLE_IMAGES["map_dagster_daemon"] = ROLE_IMAGES["map_dagster_web"]
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
    git_status_returncode: int = 0
    git_status_stdout: str = ""
    git_stderr: str = ""
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
                return self._completed(self.git_cat_file_returncode, "", self.git_stderr)
            return self._completed(
                self.git_status_returncode,
                self.git_status_stdout,
                self.git_stderr,
            )
        raise AssertionError(f"unexpected argv: {list(argv)}")

    @staticmethod
    def _completed(
        returncode: int, stdout: str, stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )


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
    pinned_root: Path
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
    # `rebuild-pinned`가 쓸어가는 root. 기본값(`Path.home()`)에 의존하지 않도록 명시하고,
    # n150 rehearsal처럼 runner ancestor floor 안에 둔다 — 그래야 R1-2 배제가 floor 규칙에
    # 가려지지 않고 단독으로 검증된다.
    pinned_root = floor / "pinned"
    pinned_root.mkdir(mode=0o700)
    (pinned_root / PROJECT).mkdir(mode=0o700)
    monkeypatch.setattr(capture, "REQUIRED_EUID", os.geteuid())
    monkeypatch.setattr(capture, "RUNNER_FILE_UID", os.geteuid())
    monkeypatch.setattr(capture, "RUNNER_FILE_GID", os.getgid())
    monkeypatch.setattr(capture, "RUNNER_ANCESTOR_FLOOR", floor)
    return Bench(
        manifest=project_dir / c6c_deployment.PAIR_MANIFEST_FILENAME,
        floor=floor,
        map_checkout=map_checkout,
        pinvi_checkout=pinvi_checkout,
        pinned_root=pinned_root / PROJECT,
        environment={
            "KTDM_C6C_CONTRACT_GENERATION": GENERATION,
            "COMPOSE_PROJECT_NAME": PROJECT,
            # flag도 runner env도 없을 때 `c6c_state_paths`가 유도하는 기본 경로가
            # 정확히 `bench.manifest`가 되도록 고정한다(설치본 production 유도의 대역).
            "KTDM_C6C_STATE_ROOT": str(floor),
            "KTDM_PINNED_RUNTIME_STATE_ROOT": str(pinned_root),
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


def _seed_pair(
    map_image: str,
    *,
    generation: str = GENERATION,
    recorded_at: str,
) -> c6c_deployment.CompatibleImagePair:
    seed = int(map_image[-4:], 16)
    return c6c_deployment.new_image_pair(
        map_image,
        _image(0x990 + seed),
        generation,
        map_ui_image_id=_image(0x991 + seed),
        map_dagster_image_id=_image(0x992 + seed),
        map_dagster_daemon_image_id=_image(0x993 + seed),
        map_source_revision=OTHER_REVISION,
        pinvi_source_revision=OTHER_REVISION,
        recorded_at=recorded_at,
    )


ROLLBACK_SEED_IMAGE = _image(0x800)


def seed_manifest(
    bench: Bench,
    *,
    active_map_image: str,
    rollback_map_image: str = ROLLBACK_SEED_IMAGE,
    generation: str = GENERATION,
    mode: int = 0o600,
) -> bytes:
    """기존 정규 v4 manifest를 심는다.

    seed는 반드시 ``rollback != active``여야 한다. 두 slot이 같으면 승격 로직을
    통째로 지워도 재capture 테스트가 green으로 남기 때문이다(R1-4 회귀).
    """

    older = _seed_pair(
        rollback_map_image,
        generation=generation,
        recorded_at="2026-08-01T00:00:00+00:00",
    )
    newer = _seed_pair(
        active_map_image,
        generation=generation,
        recorded_at="2026-08-02T00:00:00+00:00",
    )
    manifest = c6c_deployment.manifest_with_active_pair(
        c6c_deployment.initial_pair_manifest(older),
        newer,
    )
    assert manifest.rollback != manifest.active
    payload = c6c_deployment.pair_manifest_bytes(manifest)
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


# cross-repo 회귀 게이트. 하드코딩 절대경로 대신 이 env로 runner 모듈을 지목한다.
# 값이 없으면 사본 술어만으로 검증하고, **값이 주어졌는데 실패하면 skip이 아니라 fail**한다.
RUNNER_MODULE_ENV = "KTDM_C7_RUNNER_MODULE"


def _configured_runner_module_path() -> Path | None:
    raw = os.environ.get(RUNNER_MODULE_ENV, "").strip()
    return Path(raw) if raw else None


# 계약 상수 drift 게이트. runner가 top-level 키·version·pair 9필드 중 하나라도 바꾸면
# 이 digest가 어긋나 red가 된다.
RUNNER_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "manifest_version": 4,
            "pair_keys": sorted(RUNNER_PAIR_KEYS),
            "top_keys": sorted(RUNNER_MANIFEST_TOP_KEYS),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

RUNNER_CONTRACT_PINNED_SHA256 = "0351848f17189f1d6966b5e3c3a406eb3d953ba69527393b3a6847b26d90b5b3"


def test_runner_contract_constants_are_pinned_by_digest() -> None:
    """top-level 키 집합 · ``version == 4`` · pair 9필드의 해시를 박아 drift를 잡는다."""

    assert len(RUNNER_PAIR_KEYS) == 9
    assert RUNNER_CONTRACT_SHA256 == RUNNER_CONTRACT_PINNED_SHA256
    assert set(c6c_deployment.PAIR_MANIFEST_TOP_KEYS) == set(RUNNER_MANIFEST_TOP_KEYS)
    assert set(c6c_deployment.PAIR_MANIFEST_PAIR_KEYS) == set(RUNNER_PAIR_KEYS)


@pytest.mark.skipif(
    _configured_runner_module_path() is None,
    reason=f"{RUNNER_MODULE_ENV}가 없으면 사본 술어만으로 검증한다",
)
def test_real_runner_module_accepts_the_committed_pair(bench: Bench) -> None:
    import importlib.util

    module_path = _configured_runner_module_path()
    assert module_path is not None
    assert module_path.is_file(), (
        f"{RUNNER_MODULE_ENV}={module_path} is not a readable file; "
        "point it at the Map repository's scripts/lib/c7_prod_attestation.py or unset it"
    )
    spec = importlib.util.spec_from_file_location("c7_prod_attestation", module_path)
    assert spec is not None and spec.loader is not None, f"{RUNNER_MODULE_ENV} is not importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attribute in ("_validate_pair", "_exact_dict", "PAIR_RUNTIME_IMAGE_FIELDS"):
        assert hasattr(module, attribute), (
            f"{RUNNER_MODULE_ENV} module has no {attribute}; the C7 runner contract moved"
        )
    assert tuple(module.PAIR_RUNTIME_IMAGE_FIELDS) == RUNNER_PAIR_RUNTIME_IMAGE_FIELDS

    run_capture(bench)
    manifest = json.loads(bench.manifest.read_bytes())
    module._validate_pair(manifest["active"])
    module._validate_pair(manifest["rollback"])
    assert module._exact_dict(manifest, {"active", "rollback", "version"})
    assert manifest["version"] == 4


def test_cross_repo_gate_has_no_hardcoded_absolute_checkout_path() -> None:
    """하드코딩 절대경로는 n150·CI에서 조용히 skip되므로 게이트가 아니다.

    금지 fragment는 조립해서 만든다 — 이 파일 자신이 검사 대상이라 리터럴로 적으면
    검사가 스스로에게 걸린다.
    """

    source = Path(__file__).read_text(encoding="utf-8")
    forbidden = ("ktm-" + "tvn36r", "/mnt/" + "f/dev/", "F:" + "/dev/")

    for fragment in forbidden:
        assert fragment not in source, fragment
    assert RUNNER_MODULE_ENV in source


# ---------------------------------------------------------------------------
# rollback 승격 의미
# ---------------------------------------------------------------------------


def test_first_capture_duplicates_active_into_rollback(bench: Bench) -> None:
    run_capture(bench)
    manifest = json.loads(bench.manifest.read_bytes())

    assert manifest["rollback"] == manifest["active"]


def test_recapture_promotes_the_previous_active_to_rollback(bench: Bench) -> None:
    """승격 로직을 지우면 red가 되어야 한다 — seed의 두 slot이 서로 다르다."""

    previous = json.loads(seed_manifest(bench, active_map_image=_image(0x900)))
    assert previous["rollback"] != previous["active"]

    run_capture(bench)

    manifest = json.loads(bench.manifest.read_bytes())
    assert manifest["rollback"] == previous["active"]
    assert manifest["rollback"] != previous["rollback"]
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


@pytest.mark.parametrize("option", ["map_source_checkout", "pinvi_source_checkout"])
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


C7_LANE_BASENAME = "c7-compatible-pair-v4.json"


def test_the_c7_lane_basename_is_accepted(bench: Bench) -> None:
    """B-1: runner는 절대경로만 요구한다(`run-c7-prod-live-e2e.sh` 607행).

    오늘 n150의 C7 lane 스크립트는
    `E2E_C7_COMPATIBLE_PAIR_MANIFEST=/etc/kor-travel-map/c7-compatible-pair-v4.json`을
    쓴다. manager가 runner에 없는 basename 제약을 만들면 그 파일을 쓸 수 없다.
    """

    lane = bench.manifest.parent / C7_LANE_BASENAME
    assert lane.name != c6c_deployment.PAIR_MANIFEST_FILENAME

    receipt = run_capture(bench, manifest_path=str(lane))

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["manifest"] == str(lane)
    runner_validate_manifest_bytes(lane.read_bytes())
    assert not bench.manifest.exists()


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


def test_pinned_runtime_rebuild_lease_path_is_fixed() -> None:
    assert c6c_deployment.pinned_runtime_rebuild_lock_path() == (
        "/run/lock/kor-travel-docker-manager/pinned-runtime-rebuild.lock"
    )


def test_pinned_runtime_rebuild_lease_uses_real_nonblocking_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1D fixed lease도 generic helper와 같은 실제 fd 경합을 유지한다."""

    lock_path = tmp_path / "pinned-runtime-rebuild.lock"
    monkeypatch.setattr(c6c_deployment, "_PINNED_RUNTIME_REBUILD_LOCK", lock_path)
    # root requirement은 바로 아래 별도 test가 고정한다. 여기서는 현 test user가
    # 소유한 임시 file로 실제 flock 경합만 검증한다.
    monkeypatch.setattr(
        c6c_deployment,
        "_require_pinned_runtime_rebuild_root",
        lambda: None,
    )
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(c6c_deployment.DeploymentContractError) as excinfo:
            with c6c_deployment.pinned_runtime_rebuild_lock():
                pass  # pragma: no cover - holder가 있으면 enter하면 안 된다.
    finally:
        os.close(holder)

    assert str(excinfo.value) == capture.LOCK_CONTENTION_MESSAGE


def test_pinned_runtime_rebuild_lease_rejects_nonroot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(c6c_deployment.os, "geteuid", lambda: 1000)

    with pytest.raises(c6c_deployment.DeploymentContractError, match="requires root"):
        with c6c_deployment.pinned_runtime_rebuild_lock():
            pass  # pragma: no cover - root gate must reject before entering.


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
    runner.images["map_dagster_daemon"] = _image(0xBAD)
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
    """runner `_compose_container`(c7_prod_attestation.py 285-310행)와 토큰 단위 동일."""

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

    # 0번은 자기 식별(B-1), 그 다음이 manifest 경로다.
    assert lines[0] == capture.CAPTURE_CONTRACT_LINE
    assert lines[1] == f"manifest={bench.manifest}"
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
        ({"map_source_checkout": None}, None),
        ({"manifest_path": "relative/compatible-pair-v4.json"}, None),
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
        capture.CAPTURE_REFUSED_CHECKOUT_OWNERSHIP: 2,
        capture.CAPTURE_REFUSED_RUNTIME: 1,
        capture.CAPTURE_WRITE_ROLLED_BACK: 1,
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


def test_required_inputs_have_no_python_default() -> None:
    """세 입력 모두 호출자가 명시해야 한다. 기본값 결정은 함수 안에서 일어난다."""

    parameters = inspect.signature(capture.capture_compatible_pair).parameters

    for required in (
        "verified_compatible",
        "manifest_path",
        "map_source_checkout",
        "pinvi_source_checkout",
    ):
        assert parameters[required].default is inspect.Parameter.empty


def test_manifest_default_is_derived_from_c6c_state_paths(bench: Bench) -> None:
    """B-2: 세 번째 state root 규칙을 만들지 않는다 — 기본값은 이미 있는 규칙에서 온다."""

    expected, _lock = c6c_deployment.c6c_state_paths(bench.environment)
    assert expected == str(bench.manifest)

    receipt = run_capture(bench, manifest_path=None)

    assert receipt["manifest"] == expected
    assert receipt["input_sources"]["manifest_path"] == capture.MANIFEST_PATH_DERIVED_SOURCE
    runner_validate_manifest_bytes(bench.manifest.read_bytes())


def test_production_environment_derives_the_file_the_runner_already_reads(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-2 실측: 설치본이 읽는 frozen env는 production이다.

    n150의 `/opt/kor-travel-docker-manager/backend/.venv/bin/ktdctl` shim이
    `KOR_TRAVEL_DOCKER_MANAGER_PROJECT_ROOT=/opt/kor-travel-docker-manager`를
    하드코딩하므로 `get_env_path()`는 `KTDM_DEPLOYMENT_ENVIRONMENT=production`인
    `/opt/.../.env`를 읽는다. 그러면 유도값은 runner가 실제로 읽어 온 root:root 0600
    파일과 같은 자리다.
    """

    monkeypatch.setattr(c6c_deployment, "_C6C_PRODUCTION_STATE_ROOT", bench.floor)
    environment = {
        "KTDM_C6C_CONTRACT_GENERATION": GENERATION,
        "COMPOSE_PROJECT_NAME": PROJECT,
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(bench.pinned_root.parent),
    }

    receipt = run_capture(bench, manifest_path=None, environment=environment)

    assert receipt["manifest"] == str(bench.manifest)
    assert receipt["input_sources"]["manifest_path"] == capture.MANIFEST_PATH_DERIVED_SOURCE


def test_production_state_root_constant_matches_the_deployed_path() -> None:
    """ADR 근거의 경로를 코드에 박는다. 이게 바뀌면 ADR 서술도 함께 틀린다."""

    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "COMPOSE_PROJECT_NAME": "kor-travel-docker-manager",
    }
    manifest, _lock = c6c_deployment.c6c_state_paths(values)

    assert manifest == (
        "/var/lib/kor-travel-docker-manager/kor-travel-docker-manager/compatible-pair-v4.json"
    )


def test_the_forbidden_manifest_env_name_is_not_a_capture_fallback() -> None:
    """B-4: 이 키를 production `.env`에 넣으면 모든 Manager mutation이 죽는다.

    `c6c_state_paths`는 manifest와 host-global lock 경로를 함께 정하므로,
    production에서 이 키가 있으면 `c6c_deployment_lock_from_environment()`를 잡는
    mutation 전부가 같은 예외로 죽는다. capture가 이 키를 자체 fallback으로 읽으면
    "런북을 통과시키려면 그 키를 넣어라"는 잘못된 조언을 유도한다.
    """

    assert capture.MANIFEST_PATH_FORBIDDEN_ENV_NAME not in capture.MANIFEST_PATH_ENV_NAMES

    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "COMPOSE_PROJECT_NAME": "kor-travel-docker-manager",
        capture.MANIFEST_PATH_FORBIDDEN_ENV_NAME: (
            "/etc/kor-travel-map/c7-compatible-pair-v4.json"
        ),
    }
    with pytest.raises(c6c_deployment.DeploymentContractError) as excinfo:
        c6c_deployment.c6c_state_paths(values)

    assert "production C6c manifest and global lock paths are fixed" in str(excinfo.value)


def test_underivable_default_manifest_path_names_the_landmine(bench: Bench) -> None:
    runner = FakeDockerGit()
    environment = {
        key: value
        for key, value in bench.environment.items()
        if key != "KTDM_C6C_STATE_ROOT"
    }
    environment["KTDM_DEPLOYMENT_ENVIRONMENT"] = "production"
    environment[capture.MANIFEST_PATH_FORBIDDEN_ENV_NAME] = str(bench.manifest)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, manifest_path=None, environment=environment)

    message = str(excinfo.value)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert capture.MANIFEST_PATH_OPTION in message
    assert capture.MANIFEST_PATH_ENV_NAMES[0] in message
    assert capture.MANIFEST_PATH_FORBIDDEN_ENV_NAME in message
    assert "nothing was written" in message
    assert runner.argv == []


# ---------------------------------------------------------------------------
# R1-1/R2-1 런북 문자 그대로의 호출 — 세 입력의 frozen environment fallback
# ---------------------------------------------------------------------------


def _frozen_environment_with_inputs(bench: Bench, **overrides: str) -> dict[str, str]:
    values = {
        **bench.environment,
        "E2E_C7_COMPATIBLE_PAIR_MANIFEST": str(bench.manifest),
        "KTDM_C7_MAP_SOURCE_CHECKOUT": str(bench.map_checkout),
        "KTDM_C7_PINVI_SOURCE_CHECKOUT": str(bench.pinvi_checkout),
    }
    values.update(overrides)
    return values


def test_runbook_literal_invocation_commits_from_the_frozen_environment(bench: Bench) -> None:
    """`capture --verified-compatible --build`가 인자 없이 docker까지 도달해 커밋한다."""

    runner = FakeDockerGit()

    receipt = run_capture(
        bench,
        runner=runner,
        manifest_path=None,
        map_source_checkout=None,
        pinvi_source_checkout=None,
        build_flag=True,
        environment=_frozen_environment_with_inputs(bench),
    )

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["returncode"] == 0
    assert [argv for argv in runner.argv if argv[0] == "docker"]
    assert receipt["manifest"] == str(bench.manifest)
    assert receipt["input_sources"] == {
        "manifest_path": "E2E_C7_COMPATIBLE_PAIR_MANIFEST",
        "map_source_checkout": "KTDM_C7_MAP_SOURCE_CHECKOUT",
        "pinvi_source_checkout": "KTDM_C7_PINVI_SOURCE_CHECKOUT",
    }
    runner_validate_manifest_bytes(bench.manifest.read_bytes())


def test_the_runner_env_name_wins_over_the_derived_default(bench: Bench) -> None:
    """정본 소유자는 runner다. runner가 읽는 이름이 유도 기본값보다 먼저다.

    lane이 쓰는 basename을 그대로 써서 B-1(파일명 제약 없음)도 함께 고정한다.
    """

    lane = bench.manifest.parent / C7_LANE_BASENAME
    environment = {**bench.environment, "E2E_C7_COMPATIBLE_PAIR_MANIFEST": str(lane)}

    receipt = run_capture(
        bench,
        manifest_path=None,
        map_source_checkout=None,
        pinvi_source_checkout=None,
        environment={
            **environment,
            "KTDM_C7_MAP_SOURCE_CHECKOUT": str(bench.map_checkout),
            "KTDM_C7_PINVI_SOURCE_CHECKOUT": str(bench.pinvi_checkout),
        },
    )

    assert receipt["manifest"] == str(lane)
    assert receipt["input_sources"]["manifest_path"] == "E2E_C7_COMPATIBLE_PAIR_MANIFEST"
    assert not bench.manifest.exists()


def test_cli_flags_override_the_frozen_environment(bench: Bench) -> None:
    unused = bench.manifest.parent / "unused"
    unused.mkdir(mode=0o700)
    environment = _frozen_environment_with_inputs(
        bench,
        E2E_C7_COMPATIBLE_PAIR_MANIFEST=str(unused / c6c_deployment.PAIR_MANIFEST_FILENAME),
    )

    receipt = run_capture(bench, environment=environment)

    assert receipt["manifest"] == str(bench.manifest)
    assert receipt["input_sources"]["manifest_path"] == capture.MANIFEST_PATH_OPTION
    assert not (unused / c6c_deployment.PAIR_MANIFEST_FILENAME).exists()


@pytest.mark.parametrize(
    ("option", "flag", "env_names"),
    [
        ("map_source_checkout", capture.MAP_CHECKOUT_OPTION, capture.MAP_CHECKOUT_ENV_NAMES),
        (
            "pinvi_source_checkout",
            capture.PINVI_CHECKOUT_OPTION,
            capture.PINVI_CHECKOUT_ENV_NAMES,
        ),
    ],
)
def test_missing_input_says_where_to_put_what(
    bench: Bench, option: str, flag: str, env_names: tuple[str, ...]
) -> None:
    """막다른 길 금지 — 거부 메시지가 flag와 env 이름을 둘 다 지목해야 한다."""

    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, **{option: None})

    message = str(excinfo.value)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert flag in message
    for name in env_names:
        assert name in message
    assert "nothing was written" in message
    assert runner.argv == []


def test_blank_runner_env_value_falls_through_to_the_derived_default(bench: Bench) -> None:
    environment = _frozen_environment_with_inputs(bench, E2E_C7_COMPATIBLE_PAIR_MANIFEST="   ")

    receipt = run_capture(bench, manifest_path=None, environment=environment)

    assert receipt["manifest"] == str(bench.manifest)
    assert receipt["input_sources"]["manifest_path"] == capture.MANIFEST_PATH_DERIVED_SOURCE


def test_blank_checkout_environment_value_is_treated_as_absent(bench: Bench) -> None:
    environment = _frozen_environment_with_inputs(bench, KTDM_C7_MAP_SOURCE_CHECKOUT="   ")

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, map_source_checkout=None, environment=environment)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert capture.MAP_CHECKOUT_ENV_NAMES[0] in str(excinfo.value)


def test_environment_supplied_manifest_path_is_still_canonical_and_named(bench: Bench) -> None:
    environment = _frozen_environment_with_inputs(
        bench,
        E2E_C7_COMPATIBLE_PAIR_MANIFEST="relative/compatible-pair-v4.json",
    )
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, manifest_path=None, environment=environment)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert "E2E_C7_COMPATIBLE_PAIR_MANIFEST" in str(excinfo.value)
    assert runner.argv == []


# ---------------------------------------------------------------------------
# R1-2 `rebuild-pinned`가 쓸어가는 state root 배제
# ---------------------------------------------------------------------------


def test_pair_manifest_is_one_of_the_artifacts_rebuild_pinned_retires() -> None:
    """배제 규칙의 근거. 이 관계가 깨지면 규칙 자체를 다시 봐야 한다."""

    assert c6c_deployment.PAIR_MANIFEST_FILENAME in pinned_runtime_generation.f1d_legacy_artifact_paths()


@pytest.mark.parametrize("through_environment", [False, True])
def test_manifest_inside_the_pinned_runtime_state_root_is_refused(
    bench: Bench, through_environment: bool
) -> None:
    doomed = bench.pinned_root / c6c_deployment.PAIR_MANIFEST_FILENAME
    runner = FakeDockerGit()
    overrides: dict[str, Any] = (
        {
            "manifest_path": None,
            "environment": _frozen_environment_with_inputs(
                bench,
                E2E_C7_COMPATIBLE_PAIR_MANIFEST=str(doomed),
            ),
        }
        if through_environment
        else {"manifest_path": str(doomed)}
    )

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, **overrides)

    message = str(excinfo.value)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert excinfo.value.returncode == 2
    assert "rebuild-pinned" in message
    assert str(bench.pinned_root) in message
    assert not doomed.exists()
    assert runner.argv == []


def test_manifest_nested_deeper_inside_the_pinned_state_root_is_refused(bench: Bench) -> None:
    nested = bench.pinned_root / "nested"
    nested.mkdir(mode=0o700)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(
            bench,
            manifest_path=str(nested / c6c_deployment.PAIR_MANIFEST_FILENAME),
        )

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert "rebuild-pinned" in str(excinfo.value)


def test_manifest_beside_the_pinned_state_root_is_accepted(bench: Bench) -> None:
    """배제는 root **아래**만이다. 이름이 비슷한 형제 디렉터리를 막지 않는다."""

    sibling = bench.pinned_root.parent / f"{PROJECT}-runner"
    sibling.mkdir(mode=0o700)
    assert sibling.parent == bench.pinned_root.parent

    receipt = run_capture(
        bench,
        manifest_path=str(sibling / c6c_deployment.PAIR_MANIFEST_FILENAME),
    )

    assert receipt["state"] == capture.CAPTURE_COMMITTED


# ---------------------------------------------------------------------------
# R1-3 v6 pinned generation과의 대조 (보고 전용)
# ---------------------------------------------------------------------------


def _pinned_generation_payload(**overrides: str) -> dict[str, object]:
    evidence = pinned_runtime_generation.MapApplication300CandidateEvidence(
        paired_receipt_sha256="1" * 64,
        api_receipt_sha256="2" * 64,
        candidate_git_tree="3" * 40,
        postgres_image_id=_image(0xAA3),
        dagster_config_sha256="4" * 64,
        dagster_yaml_sha256="5" * 64,
        application_contract_sha256="6" * 64,
        launch_contract_sha256="7" * 64,
    )
    payload: dict[str, object] = {
        "map_api_image_id": ROLE_IMAGES["map_api"],
        "map_ui_image_id": ROLE_IMAGES["map_ui"],
        "map_dagster_image_id": ROLE_IMAGES["map_dagster_web"],
        "map_dagster_daemon_image_id": ROLE_IMAGES["map_dagster_web"],
        "pinvi_api_image_id": ROLE_IMAGES["pinvi_api"],
        "pinvi_web_image_id": _image(0xAA1),
        "pinvi_dagster_image_id": _image(0xAA2),
        "map_source_revision": MAP_REVISION,
        "pinvi_source_revision": PINVI_REVISION,
        "map_application_head": "300",
        "map_dagster_head": "4b5a6978",
        "pinvi_head": "8877meta",
        "pinset_sha256": "d" * 64,
        "map_application_300_candidate_evidence": evidence.to_payload(),
        "recorded_at": "2026-08-10T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def seed_pinned_generation(bench: Bench, **overrides: str) -> Path:
    path = pinned_runtime_generation.pinned_runtime_manifest_path(bench.environment)
    path.write_text(
        json.dumps(
            {"version": 6, "active_generation": _pinned_generation_payload(**overrides)},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_pinned_generation_agreement_is_reported(bench: Bench) -> None:
    path = seed_pinned_generation(bench)

    receipt = run_capture(bench)

    assert receipt["pinned_generation_manifest"] == str(path)
    assert receipt["pinned_generation_agrees"] is True
    assert receipt["pinned_generation_divergent_roles"] == []
    assert "pinned_generation_agrees=true" in receipt["stdout"].splitlines()


def test_pinned_generation_divergence_is_reported_but_never_refused(bench: Bench) -> None:
    """n150 실측 상태 — v6가 다른 revision/image를 주장해도 capture는 커밋한다."""

    seed_pinned_generation(
        bench,
        map_api_image_id=_image(0xBEEF),
        map_source_revision=OTHER_REVISION,
    )

    receipt = run_capture(bench)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["returncode"] == 0
    assert receipt["pinned_generation_agrees"] is False
    assert receipt["pinned_generation_divergent_roles"] == ["map_api", "map_source_revision"]
    assert (
        "pinned_generation_agrees=false divergent=map_api,map_source_revision"
        in receipt["stdout"].splitlines()
    )


def test_pinned_generation_divergence_covers_every_capture_role(bench: Bench) -> None:
    seed_pinned_generation(
        bench,
        map_api_image_id=_image(0xB01),
        map_ui_image_id=_image(0xB02),
        map_dagster_image_id=_image(0xB03),
        map_dagster_daemon_image_id=_image(0xB04),
        pinvi_api_image_id=_image(0xB05),
        pinvi_source_revision=OTHER_REVISION,
    )

    receipt = run_capture(bench)

    assert receipt["pinned_generation_divergent_roles"] == sorted(
        [role for role, _service, _f in capture.CAPTURE_ROLES] + ["pinvi_source_revision"]
    )


def test_absent_pinned_generation_manifest_reports_unknown(bench: Bench) -> None:
    receipt = run_capture(bench)

    assert receipt["pinned_generation_agrees"] is None
    assert receipt["pinned_generation_divergent_roles"] == []
    assert "pinned_generation_agrees=unknown" in receipt["stdout"].splitlines()


def test_unreadable_pinned_generation_manifest_reports_unknown(bench: Bench) -> None:
    path = seed_pinned_generation(bench)
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)

    receipt = run_capture(bench)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["pinned_generation_agrees"] is None


def test_group_readable_pinned_generation_manifest_is_ignored(bench: Bench) -> None:
    path = seed_pinned_generation(bench)
    path.chmod(0o644)
    try:
        receipt = run_capture(bench)
    finally:
        path.chmod(0o600)

    assert receipt["pinned_generation_agrees"] is None


def test_capture_never_creates_the_pinned_runtime_state_directory(bench: Bench) -> None:
    """`read_manifest`를 그대로 쓰면 부모를 mkdir한다. capture는 절대 만들지 않는다."""

    bench.pinned_root.rmdir()

    receipt = run_capture(bench)

    assert receipt["pinned_generation_agrees"] is None
    assert not bench.pinned_root.exists()


# ---------------------------------------------------------------------------
# R2-2 쓰기 **전** runner 재검증 / 커밋 후 실패의 스냅샷 복구
# ---------------------------------------------------------------------------


def test_runner_reparse_runs_before_the_irreversible_replace(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runner 술어의 **첫 번째** 적용이 `os.replace`보다 앞이어야 한다.

    사전 검증을 지우면 첫 적용이 커밋 이후로 밀려 `os.replace`가 이미 실행된 뒤에
    거부하게 되고, 이 단언 셋이 한꺼번에 red가 된다.
    """

    original = seed_manifest(bench, active_map_image=_image(0x900))
    replaced: list[tuple[Any, Any]] = []
    real_replace = c6c_deployment.os.replace
    real_assert = capture._assert_runner_reparse
    calls = {"count": 0}

    def spy_replace(source: Any, destination: Any) -> None:
        replaced.append((source, destination))
        real_replace(source, destination)

    def flaky_assert(payload_bytes: bytes) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise c6c_deployment.DeploymentContractError("runner would reject this document")
        real_assert(payload_bytes)

    monkeypatch.setattr(c6c_deployment.os, "replace", spy_replace)
    monkeypatch.setattr(capture, "_assert_runner_reparse", flaky_assert)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert calls["count"] == 1
    assert replaced == []
    assert bench.manifest.read_bytes() == original


def test_a_document_the_runner_would_reject_is_never_written(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """naive `recorded_at`은 manager의 느슨한 검사는 통과하고 runner에서 터진다."""

    monkeypatch.setattr(
        c6c_deployment,
        "_validate_pair_manifest_contract",
        lambda manifest: None,
    )
    monkeypatch.setattr(
        capture,
        "new_image_pair",
        lambda *args, **kwargs: c6c_deployment.CompatibleImagePair(
            map_image_id=ROLE_IMAGES["map_api"],
            map_ui_image_id=ROLE_IMAGES["map_ui"],
            map_dagster_image_id=ROLE_IMAGES["map_dagster_web"],
            map_dagster_daemon_image_id=ROLE_IMAGES["map_dagster_daemon"],
            map_source_revision=MAP_REVISION,
            pinvi_image_id=ROLE_IMAGES["pinvi_api"],
            pinvi_source_revision=PINVI_REVISION,
            contract_generation=GENERATION,
            recorded_at="2026-08-19T00:00:00",
        ),
    )

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert not bench.manifest.exists()


def _fail_the_post_commit_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    """커밋 **이후**의 재읽기만 실패시킨다. 사전 검증 읽기는 그대로 둔다."""

    real_read = capture._read_runner_secure_bytes
    real_write = capture.write_pair_manifest
    committed = {"done": False}

    def spy_write(*args: Any, **kwargs: Any) -> bytes:
        payload: bytes = real_write(*args, **kwargs)
        committed["done"] = True
        return payload

    def flaky(path: Path, **kwargs: Any) -> bytes:
        if committed["done"]:
            raise OSError("post-commit re-read failed")
        return real_read(path, **kwargs)

    monkeypatch.setattr(capture, "write_pair_manifest", spy_write)
    monkeypatch.setattr(capture, "_read_runner_secure_bytes", flaky)


def test_failed_post_commit_reread_restores_the_previous_bytes(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = seed_manifest(bench, active_map_image=_image(0x900))
    _fail_the_post_commit_reread(monkeypatch)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_WRITE_ROLLED_BACK
    assert excinfo.value.returncode == 1
    assert bench.manifest.read_bytes() == original
    assert stat.S_IMODE(bench.manifest.stat().st_mode) == 0o600


def test_failed_post_commit_reread_without_a_pre_image_removes_the_artifact(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_the_post_commit_reread(monkeypatch)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_WRITE_ROLLED_BACK
    assert not bench.manifest.exists()


def test_failed_restore_is_a_different_terminal_state_than_a_restored_one(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_manifest(bench, active_map_image=_image(0x900))
    _fail_the_post_commit_reread(monkeypatch)

    def broken_restore(*args: Any, **kwargs: Any) -> None:
        raise OSError("restore failed")

    monkeypatch.setattr(capture, "restore_pair_manifest_snapshot", broken_restore)

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench)

    assert excinfo.value.state == capture.CAPTURE_WRITE_INDETERMINATE
    assert "could not be restored" in str(excinfo.value)


# ---------------------------------------------------------------------------
# R2-3 교체되는 manifest의 pre-image 증거와 generation 전환 게이트
# ---------------------------------------------------------------------------


def test_receipt_carries_the_pre_image_of_the_replaced_manifest(bench: Bench) -> None:
    original = seed_manifest(bench, active_map_image=_image(0x900))
    previous = json.loads(original)

    receipt = run_capture(bench)

    assert receipt["previous_manifest_sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["previous_active"] == previous["active"]
    assert set(receipt["previous_active"]) == set(RUNNER_PAIR_KEYS)
    assert receipt["previous_recorded_at"] == previous["active"]["recorded_at"]


def test_first_capture_reports_an_absent_pre_image(bench: Bench) -> None:
    receipt = run_capture(bench)

    assert receipt["previous_manifest_sha256"] is None
    assert receipt["previous_active"] is None
    assert receipt["previous_recorded_at"] is None


def test_contract_generation_change_is_refused_by_default(bench: Bench) -> None:
    original = seed_manifest(bench, active_map_image=_image(0x900), generation="c6c-ops-v0")
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)

    message = str(excinfo.value)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert "--allow-generation-change" in message
    assert "c6c-ops-v0" in message and GENERATION in message
    assert bench.manifest.read_bytes() == original
    assert runner.argv == []


def test_contract_generation_change_proceeds_with_the_explicit_flag(bench: Bench) -> None:
    seed_manifest(bench, active_map_image=_image(0x900), generation="c6c-ops-v0")

    receipt = run_capture(bench, allow_generation_change=True)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["allow_generation_change"] is True
    assert receipt["previous_active"]["contract_generation"] == "c6c-ops-v0"
    assert receipt["contract_generation"] == GENERATION


def test_same_generation_does_not_need_the_flag(bench: Bench) -> None:
    seed_manifest(bench, active_map_image=_image(0x900))

    receipt = run_capture(bench)

    assert receipt["state"] == capture.CAPTURE_COMMITTED
    assert receipt["allow_generation_change"] is False


# ---------------------------------------------------------------------------
# R2-5 git 하위 프로세스 env 위생과 ownership 거부의 구분
# ---------------------------------------------------------------------------


def test_capture_command_environment_drops_inherited_git_redirection() -> None:
    polluted = {name: "/somewhere/else/.git" for name in capture.GIT_ENVIRONMENT_OVERRIDES}
    polluted["PATH"] = "/usr/bin"

    sanitized = capture.capture_command_environment(polluted)

    assert sanitized == {"PATH": "/usr/bin"}
    assert set(capture.GIT_ENVIRONMENT_OVERRIDES) == {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }


def test_default_runner_does_not_leak_git_redirection_into_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """주입 fake가 아닌 **실제** 하위 프로세스로 env 위생을 확인한다."""

    for name in capture.GIT_ENVIRONMENT_OVERRIDES:
        monkeypatch.setenv(name, "/attacker/repo/.git")
    runner = capture._default_runner(str(tmp_path))

    completed = runner(
        [
            sys.executable,
            "-c",
            "import json,os;print(json.dumps({k: os.environ.get(k) for k in "
            f"{list(capture.GIT_ENVIRONMENT_OVERRIDES)!r}"
            "}))",
        ]
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == dict.fromkeys(capture.GIT_ENVIRONMENT_OVERRIDES)


@pytest.mark.parametrize("failing_command", ["cat_file", "status"])
def test_dubious_ownership_is_its_own_terminal_state(bench: Bench, failing_command: str) -> None:
    stderr = (
        "fatal: detected dubious ownership in repository at '/srv/kor-travel-map'\n"
        "To add an exception for this directory, call:\n"
    )
    runner = (
        FakeDockerGit(git_cat_file_returncode=128, git_stderr=stderr)
        if failing_command == "cat_file"
        else FakeDockerGit(git_status_returncode=128, git_stderr=stderr)
    )

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)

    message = str(excinfo.value)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_CHECKOUT_OWNERSHIP
    assert excinfo.value.returncode == 2
    assert "dubious ownership" in message
    assert "not a missing commit" in message
    assert "safe.directory" in message
    assert not bench.manifest.exists()


def test_a_missing_commit_is_not_reported_as_an_ownership_problem(bench: Bench) -> None:
    runner = FakeDockerGit(git_cat_file_returncode=1, git_stderr="")

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_RUNTIME
    assert "dubious ownership" not in str(excinfo.value)


def test_unresolvable_pinned_state_root_fails_closed_with_a_readable_reason(bench: Bench) -> None:
    """배제를 증명할 수 없으면 통과시키지 않는다. 다만 이유는 읽을 수 있어야 한다."""

    # `_COMPOSE_PROJECT_PATTERN`은 숫자로 시작해도 통과하지만 pinned state의
    # `_PROJECT_NAME`은 아니다. 그때 root를 계산할 수 없으므로 fail-closed다.
    environment = {**bench.environment, "COMPOSE_PROJECT_NAME": "9map"}
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, environment=environment)

    message = str(excinfo.value)
    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert "pinned runtime state root could not be resolved" in message
    assert "rebuild-pinned" in message
    assert runner.argv == []
    assert not bench.manifest.exists()


# ---------------------------------------------------------------------------
# B1/F-2 frozen environment 읽기도 typed refusal이다
# ---------------------------------------------------------------------------


def test_capture_reads_the_frozen_environment_when_none_is_injected(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`environment=None` 경로를 실제로 밟는다.

    이전 라운드의 모든 테스트가 `environment=`를 주입해 이 경로가 한 번도 실행되지
    않았고, 그래서 F-2 구멍이 커버되지 않았다.
    """

    seen: list[str] = []

    def fake_env_path() -> str:
        return "/nonexistent/manager/.env"

    def fake_effective(env_path: str) -> dict[str, str]:
        seen.append(env_path)
        return _frozen_environment_with_inputs(bench)

    monkeypatch.setattr(capture, "get_env_path", fake_env_path)
    monkeypatch.setattr(capture, "effective_environment", fake_effective)

    receipt = run_capture(
        bench,
        environment=None,
        manifest_path=None,
        map_source_checkout=None,
        pinvi_source_checkout=None,
    )

    assert seen == ["/nonexistent/manager/.env"]
    assert receipt["state"] == capture.CAPTURE_COMMITTED


@pytest.mark.parametrize("error_kind", ["contract", "os"])
def test_frozen_environment_failure_is_a_typed_refusal(
    bench: Bench, monkeypatch: pytest.MonkeyPatch, error_kind: str
) -> None:
    """되돌리면 raw traceback이 나가고 fence 문구도 붙지 않는다(F-2 실측 재현)."""

    error: Exception = (
        c6c_deployment.DeploymentContractError("frozen environment is not derivable")
        if error_kind == "contract"
        else OSError("env-file is unreadable")
    )

    def explode(env_path: str) -> dict[str, str]:
        raise error

    monkeypatch.setattr(capture, "effective_environment", explode)
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, environment=None)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert excinfo.value.returncode == 2
    assert str(excinfo.value).endswith(capture.FENCE_NOTICE)
    assert runner.argv == []
    assert not bench.manifest.exists()


def test_env_path_resolution_failure_is_also_a_typed_refusal(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode() -> str:
        raise OSError("Manager project root is unavailable")

    monkeypatch.setattr(capture, "get_env_path", explode)
    runner = FakeDockerGit()

    with pytest.raises(capture.PairCaptureRefusal) as excinfo:
        run_capture(bench, runner=runner, environment=None)

    assert excinfo.value.state == capture.CAPTURE_REFUSED_PRECONDITION
    assert str(excinfo.value).endswith(capture.FENCE_NOTICE)
    assert runner.argv == []


# ---------------------------------------------------------------------------
# B-5 증거는 `--json` 없이 부르는 런북 호출에서도 보여야 한다
# ---------------------------------------------------------------------------


def test_plain_stdout_carries_the_pre_image_and_the_input_sources(bench: Bench) -> None:
    """런북은 `--json` 없이 부른다. 여기서 사라지면 증거가 아니다."""

    previous = seed_manifest(bench, active_map_image=_image(0x900))
    lock = FakeLock()

    receipt = run_capture(bench, lock=lock)
    lines = receipt["stdout"].splitlines()

    assert f"previous_manifest_sha256={hashlib.sha256(previous).hexdigest()}" in lines
    assert f"previous_recorded_at={receipt['previous_recorded_at']}" in lines
    assert receipt["previous_active"] is not None
    for name, value in receipt["previous_active"].items():
        assert f"previous_active.{name}={value}" in lines
    assert "rollback_images_present=true" in lines
    assert f"input_source.manifest_path={capture.MANIFEST_PATH_OPTION}" in lines
    assert f"input_source.map_source_checkout={capture.MAP_CHECKOUT_OPTION}" in lines
    assert f"input_source.pinvi_source_checkout={capture.PINVI_CHECKOUT_OPTION}" in lines
    for effect in receipt["side_effects"]:
        assert f"side_effect={effect}" in lines
    assert any(lock.lock_path in line for line in lines)
    assert any(str(bench.manifest) in line for line in lines)


def test_plain_stdout_shows_an_unrestorable_rollback_pair(bench: Bench) -> None:
    """`rollback_images_present=false`는 "기록한 rollback을 복원할 수 없다"는 뜻이다."""

    seed_manifest(bench, active_map_image=_image(0x900))
    runner = FakeDockerGit(local_images=set(ROLE_IMAGES.values()))

    receipt = run_capture(bench, runner=runner)

    assert receipt["rollback_images_present"] is False
    assert "rollback_images_present=false" in receipt["stdout"].splitlines()


def test_first_capture_stdout_says_there_was_no_pre_image(bench: Bench) -> None:
    receipt = run_capture(bench, manifest_path=None)
    lines = receipt["stdout"].splitlines()

    assert "previous_manifest_sha256=none" in lines
    assert "previous_active=none" in lines
    assert "previous_recorded_at=none" in lines
    assert f"input_source.manifest_path={capture.MANIFEST_PATH_DERIVED_SOURCE}" in lines


def test_plain_stdout_stays_non_sensitive(bench: Bench) -> None:
    """새로 추가한 증거 줄도 receipt와 같은 비민감 집합 안에 있어야 한다."""

    seed_manifest(bench, active_map_image=_image(0x900))
    receipt = run_capture(bench)

    prefixes = {line.split("=", 1)[0] for line in receipt["stdout"].splitlines()}
    allowed = {
        "capture_contract",
        "recorded_at_preserved",
        "attestation_action",
        "manifest",
        "manifest_sha256",
        "contract_generation",
        "map_source_revision",
        "pinvi_source_revision",
        "compose_project",
        "compose_project_directory",
        "pinned_generation_agrees",
        "previous_manifest_sha256",
        "previous_recorded_at",
        "previous_active",
        "rollback_images_present",
        "side_effect",
    }
    allowed |= {f"{role}_image_id" for role, _service, _field in capture.CAPTURE_ROLES}
    allowed |= {
        "input_source.manifest_path",
        "input_source.map_source_checkout",
        "input_source.pinvi_source_checkout",
    }
    allowed |= {f"previous_active.{name}" for name in c6c_deployment.PAIR_MANIFEST_PAIR_KEYS}

    assert prefixes <= allowed


# ---------------------------------------------------------------------------
# F-1 동일 identity 재capture만 byte-멱등이다 (그 밖에는 attestation 재생성 필수)
# ---------------------------------------------------------------------------


def test_recapturing_an_unchanged_runtime_is_byte_identical(bench: Bench) -> None:
    """runner는 `manifest_sha256 == attestation[...]`를 강제한다(443-448행).

    `recorded_at`을 매번 새로 찍으면 아무것도 바뀌지 않은 재capture도 해시를 바꿔
    이미 발급된 attestation을 깨뜨린다.
    """

    first = run_capture(bench)
    first_bytes = bench.manifest.read_bytes()

    second = run_capture(bench)

    assert bench.manifest.read_bytes() == first_bytes
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert second["previous_manifest_sha256"] == first["manifest_sha256"]
    assert second["previous_recorded_at"] == json.loads(first_bytes)["active"]["recorded_at"]


def test_a_changed_runtime_stamps_a_new_recorded_at(bench: Bench) -> None:
    """멱등은 "같을 때만"이다. 한 필드라도 다르면 새 시각을 찍는다."""

    run_capture(bench)
    before = json.loads(bench.manifest.read_bytes())["active"]

    moved = FakeDockerGit(images={**ROLE_IMAGES, "map_ui": _image(0x7FF)})
    receipt = run_capture(bench, runner=moved)
    after = json.loads(bench.manifest.read_bytes())["active"]

    assert after["map_ui_image_id"] == _image(0x7FF)
    assert after["recorded_at"] != before["recorded_at"]
    assert receipt["manifest_sha256"] != receipt["previous_manifest_sha256"]


def test_a_generation_change_alone_stamps_a_new_recorded_at(bench: Bench) -> None:
    """image가 같아도 generation이 바뀌면 identity가 달라진 것이다."""

    run_capture(bench)
    before = json.loads(bench.manifest.read_bytes())["active"]

    receipt = run_capture(
        bench,
        environment={**bench.environment, "KTDM_C6C_CONTRACT_GENERATION": "c6c-ops-v2"},
        allow_generation_change=True,
    )
    after = json.loads(bench.manifest.read_bytes())["active"]

    assert after["contract_generation"] == "c6c-ops-v2"
    assert after["recorded_at"] != before["recorded_at"]
    assert receipt["manifest_sha256"] != receipt["previous_manifest_sha256"]


# ---------------------------------------------------------------------------
# B-1 자기 식별: n150 설치본의 동명 `capture`는 파괴형이다
# ---------------------------------------------------------------------------


def test_capture_contract_is_the_first_stdout_line_and_a_receipt_field(bench: Bench) -> None:
    """실행 전 확인 절차와 사후 증거가 **같은 문자열**을 근거로 삼는다.

    n150 설치본(revision `41915827…`)의 `pinvi-pair capture`는 컨테이너를 내렸다가
    force-recreate하는 옛 v4 명령이고 이 문자열이 어디에도 없다. 이 줄이 사라지면
    운영자가 "지금 도는 것이 관측기인가"를 판정할 근거를 잃는다.
    """

    receipt = run_capture(bench)

    assert capture.CAPTURE_CONTRACT == "pair-capture-v1"
    assert capture.CAPTURE_CONTRACT_LINE == f"capture_contract={capture.CAPTURE_CONTRACT}"
    assert receipt["capture_contract"] == capture.CAPTURE_CONTRACT
    assert receipt["stdout"].splitlines()[0] == capture.CAPTURE_CONTRACT_LINE


def test_the_runbook_section_documents_the_same_contract_string() -> None:
    """문서의 확인 절차와 코드의 자기 식별자가 갈라지면 절차가 거짓이 된다."""

    for relative in ("docs/docker-management.md", "docs/decisions.md"):
        text = (_REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert capture.CAPTURE_CONTRACT_LINE in text, relative


# ---------------------------------------------------------------------------
# B-2 attestation 재생성 신호
# ---------------------------------------------------------------------------


def test_attestation_bound_fields_name_what_the_runner_compares() -> None:
    """runner 443-448행이 한 `if`에서 함께 보는 값들이다.

    `manifest_sha256`만 적으면 "capture는 멱등"이라는 문장이 두 revision을 빠뜨린 채
    참처럼 보인다. 세 값 모두가 attestation 대조 대상이다.
    """

    assert capture.ATTESTATION_BOUND_FIELDS == (
        "manifest_sha256",
        "active.map_source_revision",
        "active.pinvi_source_revision",
        "active.contract_generation",
    )
    for field_name in capture.ATTESTATION_BOUND_FIELDS:
        assert field_name in capture.ATTESTATION_REGENERATION_NOTICE, field_name


def test_first_capture_demands_a_fresh_attestation(bench: Bench) -> None:
    """첫 실전 capture는 정의상 멱등이 아니다 — 기존 파일이 없으면 새 시각을 찍는다."""

    receipt = run_capture(bench)
    lines = receipt["stdout"].splitlines()

    assert receipt["recorded_at_preserved"] is False
    assert receipt["attestation_action"] == capture.ATTESTATION_REGENERATION_NOTICE
    assert "recorded_at_preserved=false" in lines
    assert f"attestation_action={capture.ATTESTATION_REGENERATION_NOTICE}" in lines


def test_a_moved_runtime_demands_a_fresh_attestation(bench: Bench) -> None:
    """Map 재배포처럼 runtime이 실제로 움직이면 sha와 `map_source_revision`이 함께 바뀐다.

    runner는 그 둘을 attestation과 **함께** 대조하므로(443-448행) 낡은 attestation은
    `compatible pair mismatch`로 죽는다. capture가 그 사실을 말하지 않으면 운영자는
    §2.3을 건너뛴 채 runner를 돌린다.
    """

    first = run_capture(bench)
    map_roles = ("map_api", "map_ui", "map_dagster_web", "map_dagster_daemon")
    moved = FakeDockerGit(
        images={
            **ROLE_IMAGES,
            **{role: _image(0x7F0 + index) for index, role in enumerate(map_roles)},
        },
        revisions={role: OTHER_REVISION for role in map_roles},
    )

    receipt = run_capture(bench, runner=moved)
    active = json.loads(bench.manifest.read_bytes())["active"]

    assert receipt["recorded_at_preserved"] is False
    assert receipt["attestation_action"] == capture.ATTESTATION_REGENERATION_NOTICE
    assert receipt["manifest_sha256"] != first["manifest_sha256"]
    assert active["map_source_revision"] == OTHER_REVISION
    assert "recorded_at_preserved=false" in receipt["stdout"].splitlines()


def test_an_idempotent_recapture_does_not_ask_for_a_new_attestation(bench: Bench) -> None:
    """byte-멱등일 때만 조용하다. 아니면 반드시 한 줄로 말한다."""

    run_capture(bench)
    receipt = run_capture(bench)
    lines = receipt["stdout"].splitlines()

    assert receipt["recorded_at_preserved"] is True
    assert receipt["attestation_action"] is None
    assert "recorded_at_preserved=true" in lines
    assert not any(line.startswith("attestation_action=") for line in lines)


# ---------------------------------------------------------------------------
# followup: runner 행 번호 포인터가 낡지 않게 잡아 둔다
# ---------------------------------------------------------------------------

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_POINTER_BEARING_FILES: tuple[str, ...] = (
    "backend/src/kor_travel_docker_manager/services/c6c_pair_capture.py",
    "backend/tests/test_c6c_pair_capture.py",
    "docs/decisions.md",
    "docs/docker-management.md",
)

# 2026-08-19 실측으로 폐기된 인용. 조립해서 만든다 — 이 파일 자신이 검사 대상이라
# 리터럴로 적으면 검사가 스스로에게 걸린다(`test_cross_repo_gate_...`와 같은 이유).
_RETIRED_RUNNER_POINTERS: tuple[str, ...] = (
    "501-" + "508행",  # health 술어 (실제 508-518)
    "428-" + "432행",  # manifest shape (실제 436 + _validate_pair 439-440)
    "305-" + "316행",  # _validate_pair 키 집합 (실제 313-325)
    "305-" + "341행",  # _validate_pair 전체 (실제 313-347)
    "112-" + "164행",  # _read_secure_file (실제 111-162)
    "112-" + "146행",  # _read_secure_file ancestor 술어 (실제 111-162)
    "277-" + "302행",  # _compose_container (실제 285-310)
    "65-" + "66행",  # _exact_dict (실제 68-69)
    "c7_prod_attestation.py:" + "623",  # manifest secure read (실제 635)
    "강제하므로(`c7_prod_attestation.py` " + "436행)",  # sha 비교 (실제 443-448)
)


@pytest.mark.parametrize("relative", _POINTER_BEARING_FILES)
def test_no_retired_runner_line_pointer_survives(relative: str) -> None:
    """포인터가 낡으면 "실측으로 확인했다"는 주장 전체의 신뢰가 깎인다."""

    text = (_REPOSITORY_ROOT / relative).read_text(encoding="utf-8")

    for pointer in _RETIRED_RUNNER_POINTERS:
        assert pointer not in text, f"{relative}: stale runner pointer {pointer!r}"


# runner 파일에서 그 행이 실제로 무엇인지. `KTDM_C7_RUNNER_MODULE`이 주어지면 대조한다.
_RUNNER_ANCHORS: tuple[tuple[int, str], ...] = (
    (30, "PAIR_RUNTIME_IMAGE_FIELDS = ("),
    (68, "def _exact_dict("),
    (111, "def _read_secure_file("),
    (285, "def _compose_container("),
    (313, "def _validate_pair("),
    (436, '_exact_dict(manifest, {"active", "rollback", "version"})'),
    (439, '_validate_pair(manifest["active"])'),
    (444, 'manifest_sha256 != attestation["compatible_pair_manifest_sha256"]'),
    (446, 'active["map_source_revision"] != source_commits["map"]'),
    (447, 'active["pinvi_source_revision"] != source_commits["pinvi"]'),
    (516, 'health.get("Status") != "healthy"'),
    (635, "manifest_bytes = secure_reader(manifest_path, 0o600)"),
)


@pytest.mark.skipif(
    _configured_runner_module_path() is None,
    reason=f"{RUNNER_MODULE_ENV}가 없으면 행 번호를 대조할 runner 원본이 없다",
)
def test_cited_runner_line_numbers_still_point_at_what_they_claim() -> None:
    module_path = _configured_runner_module_path()
    assert module_path is not None
    lines = module_path.read_text(encoding="utf-8").splitlines()

    for number, fragment in _RUNNER_ANCHORS:
        assert len(lines) >= number, f"runner has fewer than {number} lines"
        assert fragment in lines[number - 1], (
            f"{module_path}:{number} no longer contains {fragment!r}; "
            "update the cited line numbers in c6c_pair_capture.py and docs/decisions.md"
        )


@pytest.mark.skipif(
    _configured_runner_module_path() is None,
    reason=f"{RUNNER_MODULE_ENV}가 없으면 사본 술어만으로 검증한다",
)
def test_the_runner_really_compares_all_three_attestation_bound_values() -> None:
    """§2.3 attestation 재생성이 필수라는 주장의 근거를 runner 원본에서 확인한다."""

    module_path = _configured_runner_module_path()
    assert module_path is not None
    lines = module_path.read_text(encoding="utf-8").splitlines()
    raise_index = next(
        index
        for index, line in enumerate(lines)
        if 'raise AttestationError("compatible pair mismatch")' in line
    )
    block = "\n".join(lines[max(0, raise_index - 8) : raise_index])

    assert 'manifest_sha256 != attestation["compatible_pair_manifest_sha256"]' in block
    assert 'active["map_source_revision"] != source_commits["map"]' in block
    assert 'active["pinvi_source_revision"] != source_commits["pinvi"]' in block
    assert 'active["contract_generation"] != attestation["c6c_contract_generation"]' in block
