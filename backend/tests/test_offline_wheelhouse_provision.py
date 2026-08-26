from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import stat
import sys
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _module() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts/provision-ktdm-offline-wheelhouse.py"
    spec = importlib.util.spec_from_file_location("offline_wheelhouse_provision", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_generated_poetry_core_wheel_has_verified_members_and_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provision = _module()
    system = tmp_path / "dist-packages"
    package = system / "poetry/core"
    dist_info = system / "poetry_core-2.3.1.dist-info"
    package.mkdir(parents=True)
    dist_info.mkdir()
    init = package / "__init__.py"
    metadata = dist_info / "METADATA"
    wheel_metadata = dist_info / "WHEEL"
    init.write_text('__version__ = "2.3.1"\n', encoding="utf-8")
    metadata.write_text("Name: poetry-core\nVersion: 2.3.1\n", encoding="utf-8")
    wheel_metadata.write_text(
        "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(provision, "_SYSTEM_DIST_PACKAGES", system)
    monkeypatch.setattr(provision, "_read_debian_file", lambda path: path.read_bytes())

    source = provision.PoetryCoreSource(
        version="2.3.1",
        files=(init, metadata, wheel_metadata),
    )
    target = provision._write_poetry_core_wheel(tmp_path, source)

    with zipfile.ZipFile(target) as wheel:
        names = set(wheel.namelist())
        assert names == {
            "poetry/core/__init__.py",
            "poetry_core-2.3.1.dist-info/METADATA",
            "poetry_core-2.3.1.dist-info/WHEEL",
            "poetry_core-2.3.1.dist-info/RECORD",
        }
        record = wheel.read("poetry_core-2.3.1.dist-info/RECORD").decode("utf-8")

    expected_payload = init.read_bytes()
    expected_digest = base64.urlsafe_b64encode(
        hashlib.sha256(expected_payload).digest()
    ).rstrip(b"=").decode("ascii")
    assert f"poetry/core/__init__.py,sha256={expected_digest},{len(expected_payload)}" in record
    assert record.endswith("poetry_core-2.3.1.dist-info/RECORD,,\n")


def test_provision_publishes_a_new_destination_without_overwriting_existing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provision = _module()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    source_wheel = source / "dependency-1.0-py3-none-any.whl"
    source_wheel.write_bytes(b"dependency-wheel")
    source_snapshot = provision.FileSnapshot(
        device=1,
        inode=1,
        mode=0o100644,
        uid=0,
        gid=0,
        nlink=1,
        size=len(source_wheel.read_bytes()),
    )
    poetry = provision.PoetryCoreSource(version="2.3.1", files=())

    monkeypatch.setattr(provision.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provision, "_canonical_locked_directory", lambda path: path)
    monkeypatch.setattr(provision, "_canonical_destination", lambda path: path)
    monkeypatch.setattr(
        provision, "_snapshot_wheels", lambda _path: ((source_wheel, source_snapshot),)
    )
    monkeypatch.setattr(provision, "_verified_debian_poetry_core", lambda: poetry)
    monkeypatch.setattr(provision, "_verify_debian_package", lambda: None)
    monkeypatch.setattr(provision, "_revalidate_wheel_snapshots", lambda _wheels: None)
    monkeypatch.setattr(provision, "_fsync_directory", lambda _path: None)
    monkeypatch.setattr(provision, "_provision_lock", lambda _path: nullcontext())

    def copy_wheels(_wheels: object, staging: Path) -> None:
        (staging / source_wheel.name).write_bytes(source_wheel.read_bytes())

    def write_poetry_wheel(staging: Path, _source: object) -> Path:
        target = staging / "poetry_core-2.3.1-py3-none-any.whl"
        target.write_bytes(b"poetry-core-wheel")
        return target

    monkeypatch.setattr(provision, "_copy_verified_wheels", copy_wheels)
    monkeypatch.setattr(provision, "_write_poetry_core_wheel", write_poetry_wheel)
    monkeypatch.setattr(
        provision,
        "_manifest_payload",
        lambda _wheels, _poetry, _wheel: {"schema": "test"},
    )

    provision.provision_wheelhouse(
        source_wheelhouse=source,
        destination_wheelhouse=destination,
    )

    assert (destination / source_wheel.name).read_bytes() == b"dependency-wheel"
    assert (destination / "poetry_core-2.3.1-py3-none-any.whl").is_file()
    assert json.loads(
        (destination / ".ktdm-wheelhouse-provenance.json").read_text(encoding="utf-8")
    ) == {"schema": "test"}

    with pytest.raises(provision.ProvisionError, match="already exists"):
        provision.provision_wheelhouse(
            source_wheelhouse=source,
            destination_wheelhouse=destination,
        )


def test_debian_package_verification_rejects_any_reported_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provision = _module()

    monkeypatch.setattr(
        provision.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="modified package file\n", stderr=""
        ),
    )

    with pytest.raises(provision.ProvisionError, match="verification failed"):
        provision._verify_debian_package()


def test_debian_verification_uses_fixed_binaries_and_an_ambient_free_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provision = _module()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((arguments, kwargs))
        output = "install ok installed" if "-W" in arguments else ""
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(provision.subprocess, "run", run)

    provision._verify_debian_package()

    assert [arguments for arguments, _kwargs in calls] == [
        ["/usr/bin/dpkg", "--verify", "python3-poetry-core"],
        ["/usr/bin/dpkg-query", "-W", "-f=${Status}", "python3-poetry-core"],
    ]
    for _arguments, kwargs in calls:
        assert kwargs["env"] == {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }


def test_launcher_self_check_requires_a_root_locked_regular_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provision = _module()
    ancestor_calls: list[object] = []

    class FakeScript:
        parent = object()

        def is_absolute(self) -> bool:
            return True

        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=0,
                st_nlink=1,
            )

    monkeypatch.setattr(provision, "Path", lambda _value: FakeScript())
    monkeypatch.setattr(
        provision,
        "_assert_locked_ancestors",
        lambda parent: ancestor_calls.append(parent),
    )

    provision._assert_trusted_launcher()

    assert ancestor_calls == [FakeScript.parent]


def test_atomic_publish_refuses_to_replace_an_existing_destination(
    tmp_path: Path,
) -> None:
    provision = _module()
    staging = tmp_path / "staging"
    destination = tmp_path / "destination"
    staging.mkdir()
    (staging / "marker").write_text("new", encoding="utf-8")

    provision._publish_without_replacing(staging, destination)

    assert (destination / "marker").read_text(encoding="utf-8") == "new"
    competing_staging = tmp_path / "competing-staging"
    competing_staging.mkdir()
    with pytest.raises(provision.ProvisionError, match="already exists"):
        provision._publish_without_replacing(competing_staging, destination)
    assert competing_staging.is_dir()


def test_staging_residue_blocks_a_new_provision_attempt(tmp_path: Path) -> None:
    provision = _module()
    (tmp_path / ".wheelhouse.stage.crashed").mkdir()

    with pytest.raises(provision.ProvisionError, match="staging residue"):
        provision._assert_no_staging_residue(tmp_path)


def test_generated_poetry_core_wheel_refuses_a_source_filename_collision(
    tmp_path: Path,
) -> None:
    provision = _module()
    source = provision.PoetryCoreSource(version="2.3.1", files=())
    (tmp_path / "poetry_core-2.3.1-py3-none-any.whl").write_bytes(b"existing")

    with pytest.raises(provision.ProvisionError, match="already supplies"):
        provision._write_poetry_core_wheel(tmp_path, source)


@pytest.mark.parametrize(
    "filename",
    [
        "poetry_core-2.3.1-py3-none-any.whl",
        "poetry-core-99.0-py3-none-any.whl",
        "Poetry.Core-1.0-py3-none-any.whl",
        "poetry..core-3.0-py3-none-any.whl",
    ],
)
def test_source_snapshot_refuses_any_normalized_poetry_core_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    provision = _module()
    source = tmp_path / "source"
    source.mkdir()
    (source / filename).write_bytes(b"foreign-poetry-core")
    monkeypatch.setattr(
        provision,
        "_safe_file_snapshot",
        lambda _path: provision.FileSnapshot(1, 1, 0o100644, 0, 0, 1, 1),
    )

    with pytest.raises(provision.ProvisionError, match="already supplies"):
        provision._snapshot_wheels(source)
