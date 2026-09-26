from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from kor_travel_docker_manager.services import c6c_deployment, runtime_pin_registry
from kor_travel_docker_manager.services import pinned_runtime_generation as generation_module
from kor_travel_docker_manager.services.c6c_deployment import (
    _PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    DeploymentContractError,
    assert_compose_mutation_allowed,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    MapApplication300CandidateEvidence,
    PinnedRuntimeGeneration,
    PinnedRuntimeManifest,
    ensure_pinned_runtime_state_directory,
    generation_from_payload,
    generation_logical_sha256,
    load_deployment_mode,
    pinned_runtime_public_paths,
    pinned_runtime_state_paths,
    read_manifest,
    read_published_pinned_runtime_generation,
    require_rebuildable_mode,
    write_manifest,
)

_PINSET_SHA256 = "a" * 64


def _digest(seed: str) -> str:
    return seed * 64


def _revision(seed: str) -> str:
    return seed * 40


def _image_id(seed: str) -> str:
    return f"sha256:{_digest(seed)}"


def _candidate_evidence(seed: str = "a") -> MapApplication300CandidateEvidence:
    return MapApplication300CandidateEvidence(
        candidate_git_tree=_revision(seed),
        postgres_image_id=_image_id(seed),
        dagster_config_sha256=_digest(seed),
    )


def _generation(seed: str = "a") -> PinnedRuntimeGeneration:
    return PinnedRuntimeGeneration(
        map_api_image_id=_image_id(seed),
        map_ui_image_id=_image_id(seed),
        map_dagster_image_id=_image_id(seed),
        map_dagster_daemon_image_id=_image_id(seed),
        pinvi_api_image_id=_image_id(seed),
        pinvi_web_image_id=_image_id(seed),
        pinvi_dagster_image_id=_image_id(seed),
        map_source_revision=_revision(seed),
        pinvi_source_revision=_revision(seed),
        map_application_head="0084_c6c_cancel_probe_fixtures",
        map_dagster_head="dagster-1",
        pinvi_head="20260801_0050",
        pinset_sha256=_digest(seed),
        map_application_300_candidate_evidence=_candidate_evidence(seed),
        recorded_at="2026-08-06T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("environment", "lifecycle", "pinvi_environment", "required"),
    [
        ("local", "development", "development", "false"),
        ("rehearsal", "rebuildable", "production", "true"),
        ("production", "operational", "production", "true"),
    ],
)
def test_load_deployment_mode_accepts_only_typed_pairs(
    environment: str,
    lifecycle: str,
    pinvi_environment: str,
    required: str,
) -> None:
    mode = load_deployment_mode(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": environment,
            "KTDM_DEPLOYMENT_LIFECYCLE": lifecycle,
            "PINVI_ENVIRONMENT": pinvi_environment,
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": required,
        }
    )

    assert mode.environment == environment
    assert mode.lifecycle == lifecycle
    assert mode.rebuildable is (lifecycle == "rebuildable")


def test_rebuildable_rejects_production_environment_even_with_lifecycle_flag() -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "production",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    with pytest.raises(DeploymentContractError, match="environment/lifecycle"):
        require_rebuildable_mode(values)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KOR_TRAVEL_MAP_API_CACHE_TARGET_SERVICE_PRINCIPALS", '[{"id":"configured"}]'),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_SYNC_ENABLED", "true"),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_COMMAND_TOKEN", "configured"),
        ("PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_CONSUMER_ID", "other-consumer"),
    ],
)
def test_rebuildable_rejects_configured_cache_target_runtime(
    name: str, value: str
) -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        name: value,
    }

    with pytest.raises(DeploymentContractError, match="inert cache-target"):
        require_rebuildable_mode(values)


def test_rebuild_capability_allows_compose_mutation_only_in_rebuildable_mode() -> None:
    values = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
    }

    assert_compose_mutation_allowed(
        ("kor-travel-map-api", "pinvi-api"),
        environment=values,
        capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
    )

    with pytest.raises(DeploymentContractError, match="rehearsal/rebuildable"):
        assert_compose_mutation_allowed(
            ("kor-travel-map-api",),
            environment={**values, "KTDM_DEPLOYMENT_LIFECYCLE": "operational"},
            capability=_PINNED_RUNTIME_REBUILD_MUTATION_CAPABILITY,
        )


def test_pinned_runtime_state_paths_are_rebuildable_project_scoped(
    tmp_path: Path,
) -> None:
    paths = pinned_runtime_state_paths(
        {
            "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
            "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
            "PINVI_ENVIRONMENT": "production",
            "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
            "COMPOSE_PROJECT_NAME": "f1d-isolated",
            "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
        },
        pinset_sha256=_PINSET_SHA256,
    )

    ensure_pinned_runtime_state_directory(paths.state_root)

    assert paths.state_root == tmp_path / "f1d-isolated"
    assert paths.manifest == paths.state_root / "pinned-runtime-generation-v6.json"
    assert paths.pinset_sha256 == _PINSET_SHA256
    assert stat.S_IMODE(paths.state_root.stat().st_mode) == 0o700


def test_pinned_runtime_state_paths_reject_nonrebuildable_or_invalid_project(
    tmp_path: Path,
) -> None:
    common = {
        "KTDM_DEPLOYMENT_ENVIRONMENT": "rehearsal",
        "KTDM_DEPLOYMENT_LIFECYCLE": "rebuildable",
        "PINVI_ENVIRONMENT": "production",
        "KOR_TRAVEL_MAP_API_OPS_PRINCIPAL_REQUIRED": "true",
        "KTDM_PINNED_RUNTIME_STATE_ROOT": str(tmp_path),
    }

    with pytest.raises(DeploymentContractError, match="COMPOSE_PROJECT_NAME"):
        pinned_runtime_state_paths(common, pinset_sha256=_PINSET_SHA256)
    with pytest.raises(DeploymentContractError, match="environment/lifecycle"):
        pinned_runtime_state_paths(
            {
                **common,
                "COMPOSE_PROJECT_NAME": "f1d-isolated",
                "KTDM_DEPLOYMENT_LIFECYCLE": "operational",
            },
            pinset_sha256=_PINSET_SHA256,
        )


def test_manifest_is_single_active_generation_without_rollback(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "pinned-runtime-generation-v6.json"
    manifest = PinnedRuntimeManifest(version=6, active_generation=_generation())

    write_manifest(path, manifest)

    assert read_manifest(path) == manifest
    assert '"rollback"' not in path.read_text(encoding="utf-8")


def test_manifest_rejects_unsafe_file_mode(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "pinned-runtime-generation-v6.json"
    write_manifest(path, PinnedRuntimeManifest(version=6, active_generation=_generation()))
    os.chmod(path, 0o644)

    with pytest.raises(DeploymentContractError, match="unsafe"):
        read_manifest(path)


def test_private_json_write_is_not_reported_as_failed_when_only_dir_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GM-10 후속 회귀(적대적 리뷰 발견): 디렉터리 fsync는 os.replace가 이미
    성공한 뒤의 추가 durability 보장일 뿐이다. 예전에는 그 호출이 성공/실패를
    DeploymentContractError로 매핑하는 try 안에 있어서, fsync만 실패해도 이미
    끝난 쓰기를 "쓸 수 없음"으로 잘못 보고했다 — runtime_pair_rotation.py에서
    고친 것과 같은 버그 계열이 이 파일에도 있었다."""

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    path = state / "artifact.json"

    monkeypatch.setattr(
        generation_module,
        "_fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("simulated fsync failure")),
    )

    generation_module._write_private_json(path, {"a": 1}, "test artifact")

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_public_json_write_is_not_reported_as_failed_when_only_dir_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """위와 같은 회귀를 _write_public_json(dir_fd 기반 O_EXCL|O_NOFOLLOW 경로)에도
    확인한다 — 이쪽은 이미 열어 둔 dir_fd를 직접 fsync하므로 별도로 검증해야 한다."""

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    public_root = tmp_path / "public"
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(public_root))
    manifest = PinnedRuntimeManifest(version=6, active_generation=_generation())

    real_fsync = os.fsync
    call_count = 0

    def flaky_fsync(fd: int) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            real_fsync(fd)  # 임시 파일 자체의 fsync는 정상적으로 통과시킨다.
            return
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(generation_module.os, "fsync", flaky_fsync)
    private_path = state / "pinned-runtime-generation-v6.json"

    generation_module.publish_pinned_runtime_generation(manifest=manifest, private_path=private_path)

    paths = generation_module.pinned_runtime_public_paths(private_path=private_path)
    assert json.loads(paths.manifest.read_text(encoding="utf-8")) == manifest.to_payload()


_PUBLIC_ENVELOPE_KEYS = {"status", "source", "manifest", "pinset_binding", "summary"}


def test_public_generation_copy_preserves_exact_raw_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API 관측 사본은 private v6 manifest dict를 바꾸면 안 된다.

    envelope는 manifest 원문에 결박·요약만 더한다. 옛 `journal`·`terminal` 키와
    `summary.journal_version`은 ADR-51 B3에서 사라졌다.
    """

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    public_root = tmp_path / "public"
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(public_root))
    manifest = PinnedRuntimeManifest(version=6, active_generation=_generation())

    write_manifest(state / "pinned-runtime-generation-v6.json", manifest)

    paths = pinned_runtime_public_paths()
    assert paths.manifest.parent == public_root
    assert paths.manifest.exists()
    assert sorted(path.name for path in public_root.iterdir()) == [
        "pinned-runtime-generation-v6.json"
    ]
    if os.name != "nt":
        assert stat.S_IMODE(paths.manifest.stat().st_mode) == 0o644
    observed = read_published_pinned_runtime_generation()
    assert observed["status"] == "ok"
    assert set(observed) == _PUBLIC_ENVELOPE_KEYS
    assert observed["manifest"] == manifest.to_payload()
    assert "journal_version" not in observed["summary"]
    assert observed["summary"]["manifest_version"] == 6


def test_public_generation_copy_fails_closed_when_all_public_artifacts_are_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_root = tmp_path / "public"
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(public_root))
    public_root.mkdir(mode=0o755)
    (public_root / "pinned-runtime-generation-v6.json").write_text("{}", encoding="utf-8")

    observed = read_published_pinned_runtime_generation()

    assert observed["status"] == "unknown"
    assert observed["manifest"] is None
    assert set(observed) == _PUBLIC_ENVELOPE_KEYS | {"detail"}


def test_public_generation_copy_is_the_manifest_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-51 뒤 배포는 v8 journal을 쓰지 않는다 — 커밋 때 쓰는 manifest 하나가 증거다."""

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(tmp_path / "public"))

    write_manifest(
        state / "pinned-runtime-generation-v6.json",
        PinnedRuntimeManifest(version=6, active_generation=_generation()),
    )

    observed = read_published_pinned_runtime_generation()

    assert observed["status"] == "ok"
    assert observed["manifest"] is not None
    assert "journal" not in observed
    assert "terminal" not in observed


def test_public_generation_copy_without_a_manifest_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_root = tmp_path / "public"
    public_root.mkdir(mode=0o755)
    os.chmod(public_root, 0o755)
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(public_root))

    observed = read_published_pinned_runtime_generation()

    assert observed["status"] == "unknown"
    assert observed["manifest"] is None
    assert observed["pinset_binding"]["status"] == "unknown"
    assert observed["summary"]["state"] == "unknown"
    # 복구 안내는 manifest 하나만 요구한다 — 옛 `--journal` 인자는 없다.
    assert "--journal" not in observed["summary"]["next_action"]
    assert "--manifest" in observed["summary"]["next_action"]


def test_a_stale_legacy_journal_copy_is_ignored_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-51 이전 배포가 남긴 공개 v8 journal 사본은 읽지도 고치지도 않는다.

    n150의 공개 root에는 그 파일(`pinned-runtime-rebuild-v8.json`)이 아직 있다. reader가
    그것을 열어 해석하면 옛 흐름의 불일치가 `pin verify`를 1로 만들고 그것을 요구하는
    M05 하네스가 막힌다(B2 적대 리뷰 M1). 이제 reader는 manifest만 보므로 그 파일의
    내용이 무엇이든 결과가 같아야 한다.
    """

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    public_root = tmp_path / "public"
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(public_root))
    manifest = PinnedRuntimeManifest(version=6, active_generation=_generation("a"))
    write_manifest(state / "pinned-runtime-generation-v6.json", manifest)
    stale = public_root / "pinned-runtime-rebuild-v8.json"
    stale.write_bytes(b'{"version": 8}')
    os.chmod(stale, 0o644)

    observed = read_published_pinned_runtime_generation()

    assert observed["status"] == "ok"
    assert observed["manifest"] == manifest.to_payload()
    assert "journal" not in observed
    assert stale.read_bytes() == b'{"version": 8}'


@pytest.mark.parametrize("unsafe", ["symlink", "writable"])
def test_public_generation_writer_rejects_an_unsafe_public_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    public_root = tmp_path / "public"
    if unsafe == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o755)
        public_root.symlink_to(target, target_is_directory=True)
    else:
        public_root.mkdir(mode=0o755)
        os.chmod(public_root, 0o777)
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(public_root))

    with pytest.raises(
        DeploymentContractError,
        match="canonical absolute path|public copy directory is unsafe",
    ):
        write_manifest(
            state / "pinned-runtime-generation-v6.json",
            PinnedRuntimeManifest(version=6, active_generation=_generation()),
        )


def test_public_generation_binding_distinguishes_current_pair_and_pending_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """manifest만 읽는 결박은 `match`·`pending_rebuild`·`unknown` 셋뿐이다.

    옛 `drift`는 진행 중인 journal이 있어야 성립했다. manifest는 커밋 때만 쓰이므로
    registry와 다른 pair는 곧 "회전했고 아직 배포 전"이다 — `pin verify`가 이 결박
    때문에 1로 끝나지 않아야 M05 하네스가 회전 직후에도 돈다.
    """

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    monkeypatch.setenv("KTDM_PINNED_RUNTIME_PUBLIC_ROOT", str(tmp_path / "public"))
    generation = _generation()
    write_manifest(
        state / "pinned-runtime-generation-v6.json",
        PinnedRuntimeManifest(version=6, active_generation=generation),
    )
    matching: dict[str, object] = {
        "status": "ok",
        "pinset_sha256": generation.pinset_sha256,
        "sources": [
            {"role": "map", "revision": generation.map_source_revision},
            {"role": "pinvi", "revision": generation.pinvi_source_revision},
        ],
    }

    def observe(registry: dict[str, object]) -> dict[str, Any]:
        monkeypatch.setattr(
            runtime_pin_registry, "read_published_runtime_pins", lambda: registry
        )
        return read_published_pinned_runtime_generation()

    observed = observe(matching)
    assert observed["pinset_binding"] == {
        "status": "match",
        "registry_pinset_sha256": generation.pinset_sha256,
        "generation_pinset_sha256": generation.pinset_sha256,
    }
    assert observed["summary"]["state"] == "committed"

    # 새 pair로 회전한 직후: 마지막 커밋 세대는 이전 pinset·revision을 가리킨다.
    for rotated in (
        {**matching, "pinset_sha256": "f" * 64},
        {
            **matching,
            "sources": [
                {"role": "map", "revision": "f" * 40},
                {"role": "pinvi", "revision": generation.pinvi_source_revision},
            ],
        },
    ):
        observed = observe(rotated)
        assert observed["pinset_binding"]["status"] == "pending_rebuild"
        assert observed["pinset_binding"]["generation_pinset_sha256"] == (
            generation.pinset_sha256
        )
        assert observed["summary"]["state"] == "pending_rebuild"

    # registry를 믿을 수 없거나 모양이 틀리면 값을 추측하지 않는다.
    for unreadable in (
        {**matching, "status": "degraded"},
        {"status": "unknown"},
        {**matching, "sources": None},
        {**matching, "pinset_sha256": None},
    ):
        observed = observe(unreadable)
        assert observed["pinset_binding"]["status"] == "unknown"
        assert observed["pinset_binding"]["registry_pinset_sha256"] is None
        assert observed["summary"]["state"] == "unverified"


def test_generation_logical_sha256_excludes_recording_timestamp() -> None:
    initial = _generation()
    later = generation_from_payload(
        {**initial.to_payload(), "recorded_at": "2026-08-06T01:00:00+00:00"}
    )

    assert generation_logical_sha256(initial) == generation_logical_sha256(later)


def test_v4_manifest_api_is_absent_and_only_tombstoned() -> None:
    for name in (
        "CompatibleImagePair",
        "CompatiblePairManifest",
        "parse_pair_manifest",
        "initial_pair_manifest",
        "write_pair_manifest",
        "restore_pair_manifest_snapshot",
    ):
        assert not hasattr(c6c_deployment, name)


def test_document_versions_are_frozen() -> None:
    """manifest v6은 n150의 on-disk 파일과 M05 driver가 읽는 버전이다.

    ADR-51 D에서 v6 쓰기가 멈출 때까지 바꾸지 않는다 — 바꾸면 호스트에 이미 있는
    manifest를 읽지 못해 M05 preflight가 막힌다.
    """

    assert generation_module._MANIFEST_VERSION == 6
