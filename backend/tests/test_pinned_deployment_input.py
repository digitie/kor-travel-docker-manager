from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import pinned_deployment_input as input_module
from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
    CacheTargetProductionPinManifest,
)
from kor_travel_docker_manager.services.map_service_contract import (
    C6C_CANCEL_PROBE_CAPABILITY_GENERATION,
)
from kor_travel_docker_manager.services.pinned_deployment_input import (
    PinnedDeploymentInputPaths,
    _initial_journal,
    _render_v2_env,
    _validate_journal,
    install_pinned_deployment_inputs,
    pinned_deployment_repo_specs,
    production_pinset_sha256,
)
from kor_travel_docker_manager.services.pinned_source_install import RepoSpec


def _paths(tmp_path: Path) -> PinnedDeploymentInputPaths:
    state_directory = tmp_path / "pinned-deployment-inputs-v2"
    history_directory = state_directory / "history"
    generation_directory = history_directory / ("f" * 64)
    return PinnedDeploymentInputPaths(
        state_directory=state_directory,
        generation_directory=generation_directory,
        journal=generation_directory / "pinned-deployment-input-v2.json",
        backup=generation_directory / "pinned-deployment-input-v2.env.backup",
        sources_directory=state_directory / "sources",
        history_directory=history_directory,
    )


def test_v2_pinset_digest_uses_manifest_canonical_serialization() -> None:
    assert production_pinset_sha256() == CACHE_TARGET_PRODUCTION_PINS.pinset_sha256
    assert len(production_pinset_sha256()) == 64


def _write_trusted_service_provenance(
    paths: PinnedDeploymentInputPaths,
    *,
    c6c_generation: int = C6C_CANCEL_PROBE_CAPABILITY_GENERATION,
) -> tuple[CacheTargetProductionPinManifest, tuple[RepoSpec, RepoSpec]]:
    service_bytes = b'{"openapi":"3.1.0"}\n'
    pins = replace(
        CACHE_TARGET_PRODUCTION_PINS,
        service_openapi_sha256=hashlib.sha256(service_bytes).hexdigest(),
    )
    map_root = paths.sources_directory / "map" / pins.map_release_revision
    pinvi_root = paths.sources_directory / "pinvi" / pins.pinvi_release_revision
    map_service = map_root / "packages/kor-travel-map-api/openapi.service.json"
    pinvi_service = pinvi_root / "apps/api/tests/contract/kor-travel-map-openapi-service.json"
    provenance = pinvi_root / "contracts/kor-travel-map-service-provenance-v1.json"
    for path in (map_service, pinvi_service, provenance):
        path.parent.mkdir(parents=True, exist_ok=True)
    map_service.write_bytes(service_bytes)
    pinvi_service.write_bytes(service_bytes)
    provenance.write_text(
        json.dumps(
            {
                "capabilities": {
                    "cache_target": {"generation": int(pins.contract_generation)},
                    "c6c_cancel_probe": {"generation": c6c_generation},
                },
                "map_release_revision": pins.map_release_revision,
                "service_openapi_sha256": pins.service_openapi_sha256,
                "version": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    specs = (
        RepoSpec(
            "map",
            "KOR_TRAVEL_MAP_REPO_DIR",
            "KOR_TRAVEL_MAP_GIT_COMMIT",
            "https://github.com/digitie/kor-travel-map.git",
            pins.map_release_revision,
        ),
        RepoSpec(
            "pinvi",
            "PINVI_REPO_DIR",
            "PINVI_SOURCE_REVISION",
            "https://github.com/digitie/pinvi.git",
            pins.pinvi_release_revision,
        ),
    )
    return pins, specs


def test_pinned_artifact_preflight_requires_general_service_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    pins, specs = _write_trusted_service_provenance(paths)
    monkeypatch.setattr(input_module, "CACHE_TARGET_PRODUCTION_PINS", pins)
    monkeypatch.setattr(input_module, "_require_root_owned_file", lambda *_args, **_kwargs: None)
    verified: list[Path] = []
    monkeypatch.setattr(
        input_module,
        "_validate_existing_pinned_worktree",
        lambda path, **_kwargs: verified.append(path),
    )

    input_module._verify_pinned_artifacts(
        paths=paths,
        specs=specs,
        runner=lambda *_args, **_kwargs: pytest.fail("git must not run"),
    )

    assert verified == [
        paths.sources_directory / "map" / pins.map_release_revision,
        paths.sources_directory / "pinvi" / pins.pinvi_release_revision,
    ]


def test_pinned_artifact_preflight_rejects_c6c_capability_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    pins, specs = _write_trusted_service_provenance(
        paths,
        c6c_generation=C6C_CANCEL_PROBE_CAPABILITY_GENERATION + 1,
    )
    monkeypatch.setattr(input_module, "CACHE_TARGET_PRODUCTION_PINS", pins)
    monkeypatch.setattr(input_module, "_require_root_owned_file", lambda *_args, **_kwargs: None)

    with pytest.raises(DeploymentContractError, match="service provenance differs"):
        input_module._verify_pinned_artifacts(
            paths=paths,
            specs=specs,
            runner=lambda *_args, **_kwargs: pytest.fail("git must not run"),
        )


def test_v2_env_render_replaces_every_deployment_scalar_atomically(tmp_path: Path) -> None:
    specs = pinned_deployment_repo_specs()
    raw = (
        b"KOR_TRAVEL_MAP_REPO_DIR=/legacy/map\n"
        b"KOR_TRAVEL_MAP_GIT_COMMIT=c0afaa4e318a2e2e6d85f53bb889af3e6adec8c1\n"
        b"PINVI_REPO_DIR=/legacy/pinvi\n"
        b"PINVI_SOURCE_REVISION=3ff54b8b15965c6ecd5c55b1419208e65831c7fe\n"
        b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256="
        b"144b4335d98fc021368b3297f5b8ed7b1c560e9850ebbdd8af71e45623ba7b3d\n"
        b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION="
        b"e12494bd5c4b5b2e1d51c72b6ddcf18eead0e53f\n"
        b"PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION=7\n"
        b"UNRELATED=value\n"
    )

    rendered = _render_v2_env(raw, paths=_paths(tmp_path), specs=specs).decode()

    assert "UNRELATED=value\n" in rendered
    assert "KOR_TRAVEL_MAP_GIT_COMMIT=8c5bdcf8ce892439a8bb8e0013edf74127bf076a\n" in rendered
    assert "PINVI_SOURCE_REVISION=3b87c19cc78a07121c27df7d7a4c382c2d3aa068\n" in rendered
    assert "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD=0083_nonderived_uuid_generator\n" in rendered
    assert (
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256="
        "c7838b20bd70bf333590cb440a705dd7e893f9e366078d6c11200d701d40bdcd\n"
    ) in rendered
    assert (
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION="
        "8c5bdcf8ce892439a8bb8e0013edf74127bf076a\n"
    ) in rendered


def test_v2_journal_rejects_pinset_or_repository_schema_drift() -> None:
    raw = b"old"
    rendered = b"new"
    journal = _initial_journal(
        raw=raw,
        rendered=rendered,
        expected_owner_uid=1,
        expected_owner_gid=2,
    )
    assert journal["old_env_sha256"] == hashlib.sha256(raw).hexdigest()
    _validate_journal(journal)

    with pytest.raises(DeploymentContractError, match="schema"):
        _validate_journal({**journal, "unexpected": True})
    with pytest.raises(DeploymentContractError, match="digest"):
        _validate_journal({**journal, "new_pinset_sha256": "invalid"})


def test_env_replace_crash_resumes_by_durably_marking_env_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    old = b"old"
    new = b"new"
    prepared = _initial_journal(
        raw=old,
        rendered=new,
        expected_owner_uid=1,
        expected_owner_gid=2,
    )
    prepared["repositories"] = [
        {
            "label": "map",
            "revision": "a" * 40,
            "target_root": str(paths.sources_directory / "map" / ("a" * 40)),
            "tree": "b" * 40,
        },
        {
            "label": "pinvi",
            "revision": "c" * 40,
            "target_root": str(paths.sources_directory / "pinvi" / ("c" * 40)),
            "tree": "d" * 40,
        },
    ]
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(input_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(input_module, "_ensure_root_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(input_module, "pinned_deployment_input_paths", lambda _values: paths)
    monkeypatch.setattr(input_module, "read_canonical_env_file", lambda *_args, **_kwargs: new)
    monkeypatch.setattr(input_module, "pinned_deployment_repo_specs", lambda: ())
    monkeypatch.setattr(input_module, "_path_exists_lstat", lambda path: path == paths.journal)
    monkeypatch.setattr(input_module, "_read_journal", lambda _path: prepared)
    monkeypatch.setattr(input_module, "_verify_journal", lambda **_kwargs: None)
    monkeypatch.setattr(
        input_module,
        "_write_journal",
        lambda _path, journal: writes.append(dict(journal)),
    )
    monkeypatch.setattr(input_module, "_verify_pinned_artifacts", lambda **_kwargs: None)
    monkeypatch.setattr(
        input_module,
        "replace_canonical_env_file",
        lambda **_kwargs: pytest.fail("already-replaced env must not be replaced again"),
    )

    result = install_pinned_deployment_inputs(
        environment={"KTDM_DEPLOYMENT_ENVIRONMENT": "production"},
        env_path=tmp_path / ".env",
        env_bytes=new,
        expected_owner_uid=1,
        expected_owner_gid=2,
        runner=lambda *_args, **_kwargs: pytest.fail("git must not run"),
    )

    assert result == {"success": True, "state": "handoff_pending", "resumed": False}
    assert writes[0]["phase"] == "env_replaced"


def test_rolled_back_future_generation_revalidates_its_v2_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B가 A로 rollback된 뒤 B 재시도는 legacy v1 검증으로 빠지면 안 된다."""

    current_paths = _paths(tmp_path)
    predecessor_paths = PinnedDeploymentInputPaths(
        state_directory=current_paths.state_directory,
        generation_directory=current_paths.history_directory / ("a" * 64),
        journal=current_paths.history_directory / ("a" * 64) / "pinned-deployment-input-v2.json",
        backup=current_paths.history_directory / ("a" * 64) / "pinned-deployment-input-v2.env.backup",
        sources_directory=current_paths.sources_directory,
        history_directory=current_paths.history_directory,
    )
    predecessor = _initial_journal(
        raw=b"legacy",
        rendered=b"pinset-a-env",
        expected_owner_uid=1,
        expected_owner_gid=2,
    )
    predecessor["new_pinset_sha256"] = "a" * 64
    predecessor["phase"] = "f1d_completed"
    rolled_back = _initial_journal(
        raw=b"pinset-a-env",
        rendered=b"pinset-b-env",
        expected_owner_uid=1,
        expected_owner_gid=2,
        old_pinset_sha256="a" * 64,
    )
    rolled_back["phase"] = "rolled_back"
    archived = Mock(return_value="a" * 64)
    legacy = Mock(side_effect=pytest.fail)
    monkeypatch.setattr(
        input_module,
        "_find_terminal_v2_predecessor",
        lambda **_kwargs: (predecessor_paths, predecessor),
    )
    monkeypatch.setattr(input_module, "_archive_completed_v2_predecessor", archived)
    monkeypatch.setattr(input_module, "_verify_legacy_v1_predecessor", legacy)

    input_module._verify_rolled_back_predecessor(
        paths=current_paths,
        journal=rolled_back,
        environment={},
        raw=b"pinset-a-env",
        expected_owner_uid=1,
        expected_owner_gid=2,
        runner=lambda *_args, **_kwargs: pytest.fail("git must not run"),
    )

    archived.assert_called_once()
    legacy.assert_not_called()
