"""F1F v2의 trusted pinned deployment-input transaction.

이 모듈은 source checkout, Map migration head, PinVi cache-target contract를 한 canonical
``.env`` 교체로 수렴한다. Docker/Compose/DB/runtime을 import하거나 실행하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    c6c_state_paths,
)
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    TERMINAL_PHASES as DIAGNOSTIC_TERMINAL_PHASES,
)
from kor_travel_docker_manager.services.cache_target_diagnostics import (
    read_cache_target_diagnostic,
)
from kor_travel_docker_manager.services.cache_target_enable import (
    read_canonical_env_file,
    read_enable_cutover_journal,
    replace_canonical_env_file,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
)
from kor_travel_docker_manager.services.cache_target_window import (
    TERMINAL_PHASES as WINDOW_TERMINAL_PHASES,
)
from kor_travel_docker_manager.services.cache_target_window import (
    read_cache_target_window,
)
from kor_travel_docker_manager.services.map_service_contract import (
    C6C_CANCEL_PROBE_CAPABILITY_GENERATION,
)
from kor_travel_docker_manager.services.pinned_drift_bootstrap import (
    archive_terminal_legacy_pinned_drift_bootstrap,
    archive_terminal_pinned_drift_bootstrap,
    read_pinned_drift_bootstrap,
)
from kor_travel_docker_manager.services.pinned_source_install import (
    GitRunner,
    PinnedSourceInstallPaths,
    RepoSpec,
    _ensure_root_directory,
    _fsync_directory,
    _materialize_pinned_worktree,
    _path_exists_lstat,
    _read_private_file,
    _validate_existing_pinned_worktree,
    _write_private_backup,
)

_MAX_ENV_BYTES = 1_048_576
_MAX_JOURNAL_BYTES = 65_536
_STATE_DIRECTORY_NAME = "pinned-deployment-inputs-v2"
_JOURNAL_NAME = "pinned-deployment-input-v2.json"
_BACKUP_NAME = "pinned-deployment-input-v2.env.backup"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DOTENV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)")
_PHASES = frozenset(
    {
        "prepared",
        "env_replaced",
        "handoff_pending",
        "f1d_in_progress",
        "f1d_completed",
        "rollback_preparing",
        "rolled_back",
    }
)
_TERMINAL_PAIR_PHASES = frozenset({"f1d_completed", "rolled_back"})
_LEGACY_MAP_REVISION = "c0afaa4e318a2e2e6d85f53bb889af3e6adec8c1"
_LEGACY_PINVI_REVISION = "3ff54b8b15965c6ecd5c55b1419208e65831c7fe"
_LEGACY_SERVICE_SHA256 = "144b4335d98fc021368b3297f5b8ed7b1c560e9850ebbdd8af71e45623ba7b3d"
_LEGACY_MAP_SOURCE_REVISION = "e12494bd5c4b5b2e1d51c72b6ddcf18eead0e53f"
_LEGACY_CONTRACT_GENERATION = "7"
_LEGACY_MAP_ALEMBIC_HEAD = "0082_legacy_write_fence"


@dataclass(frozen=True)
class PinnedDeploymentInputPaths:
    state_directory: Path
    generation_directory: Path
    journal: Path
    backup: Path
    sources_directory: Path
    history_directory: Path


def pinned_deployment_input_paths(values: Mapping[str, str]) -> PinnedDeploymentInputPaths:
    """현재 pinset의 immutable v2 generation state path를 계산한다."""

    return _pinned_deployment_input_paths_for_pinset(values, production_pinset_sha256())


def _pinned_deployment_input_paths_for_pinset(
    values: Mapping[str, str], pinset_sha256: str
) -> PinnedDeploymentInputPaths:
    if _SHA256.fullmatch(pinset_sha256) is None:
        raise DeploymentContractError("pinned deployment input pinset digest is invalid")
    manifest_path, _ = c6c_state_paths(values)
    state_directory = Path(manifest_path).parent / _STATE_DIRECTORY_NAME
    history_directory = state_directory / "history"
    generation_directory = history_directory / pinset_sha256
    return PinnedDeploymentInputPaths(
        state_directory=state_directory,
        generation_directory=generation_directory,
        journal=generation_directory / _JOURNAL_NAME,
        backup=generation_directory / _BACKUP_NAME,
        sources_directory=state_directory / "sources",
        history_directory=history_directory,
    )


def production_pinset_sha256() -> str:
    """manifest semantic fields의 canonical v2 identity를 계산한다."""

    return CACHE_TARGET_PRODUCTION_PINS.pinset_sha256


def legacy_predecessor_pinset_sha256() -> str:
    """F1F가 수용하는 유일한 v1 semantic pinset identity다."""

    return hashlib.sha256(
        json.dumps(
            {
                "version": 1,
                "map_release_revision": _LEGACY_MAP_REVISION,
                "pinvi_release_revision": _LEGACY_PINVI_REVISION,
                "service_openapi_sha256": _LEGACY_SERVICE_SHA256,
                "contract_generation": _LEGACY_CONTRACT_GENERATION,
                "map_application_alembic_head": _LEGACY_MAP_ALEMBIC_HEAD,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def pinned_deployment_repo_specs() -> tuple[RepoSpec, RepoSpec]:
    pins = CACHE_TARGET_PRODUCTION_PINS
    specs = (
        RepoSpec(
            label="map",
            source_key="KOR_TRAVEL_MAP_REPO_DIR",
            revision_key="KOR_TRAVEL_MAP_GIT_COMMIT",
            canonical_url="https://github.com/digitie/kor-travel-map.git",
            revision=pins.map_release_revision,
        ),
        RepoSpec(
            label="pinvi",
            source_key="PINVI_REPO_DIR",
            revision_key="PINVI_SOURCE_REVISION",
            canonical_url="https://github.com/digitie/pinvi.git",
            revision=pins.pinvi_release_revision,
        ),
    )
    if any(_REVISION.fullmatch(spec.revision) is None for spec in specs):
        raise DeploymentContractError("pinned deployment input source revision is invalid")
    return specs


def install_pinned_deployment_inputs(
    *,
    environment: Mapping[str, str],
    env_path: Path,
    env_bytes: bytes,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner,
) -> dict[str, Any]:
    """exact predecessor만 v2 handoff-pending input으로 원자 전환한다."""

    if os.geteuid() != 0:
        raise DeploymentContractError("pinned deployment input installation requires root")
    if environment.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "production":
        raise DeploymentContractError(
            "pinned deployment input installation is available only in production"
        )
    if env_bytes != read_canonical_env_file(
        env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    ):
        raise DeploymentContractError("canonical env changed after frozen snapshot")

    paths = pinned_deployment_input_paths(environment)
    _ensure_root_directory(paths.state_directory, mode=0o700)
    _ensure_root_directory(paths.history_directory, mode=0o700)
    specs = pinned_deployment_repo_specs()
    journal = _read_journal(paths.journal) if _path_exists_lstat(paths.journal) else None
    archived_v2_predecessor_pinset: str | None = None
    if journal is None:
        predecessor = _find_terminal_v2_predecessor(
            paths=paths,
            env_bytes=env_bytes,
        )
    else:
        predecessor = None
    if predecessor is not None:
        predecessor_paths, predecessor_journal = predecessor
        archived_v2_predecessor_pinset = _archive_completed_v2_predecessor(
            paths=predecessor_paths,
            journal=predecessor_journal,
            environment=environment,
            env_bytes=env_bytes,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
            runner=runner,
        )
    if journal is not None:
        recover_replaced_env = _prepared_env_replacement_needs_recovery(
            journal=journal,
            env_path=env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
        _verify_journal(
            paths=paths,
            journal=journal,
            env_path=env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
            specs=specs,
            runner=runner,
        )
        if recover_replaced_env:
            if not journal["repositories"]:
                raise DeploymentContractError(
                    "prepared pinned deployment input cannot recover an unverified env replacement"
                )
            journal = _transition(journal, "env_replaced")
            _write_journal(paths.journal, journal)
        if journal["phase"] == "handoff_pending":
            return {"success": True, "state": "handoff_pending", "resumed": True}
        if journal["phase"] in {"f1d_in_progress", "f1d_completed"}:
            raise DeploymentContractError("pinned deployment input handoff already started")
        if journal["phase"] == "rolled_back":
            _verify_rolled_back_predecessor(
                paths=paths,
                journal=journal,
                environment=environment,
                raw=env_bytes,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
                runner=runner,
            )
            rendered = _render_v2_env(env_bytes, paths=paths, specs=specs)
            journal = _initial_journal(
                raw=env_bytes,
                rendered=rendered,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
                old_pinset_sha256=str(journal["old_pinset_sha256"]),
            )
            _ensure_private_backup(paths.backup, env_bytes)
            _write_journal(paths.journal, journal)
    else:
        if (
            archived_v2_predecessor_pinset is None
            and (_path_exists_lstat(paths.backup) or _path_exists_lstat(paths.sources_directory))
        ):
            raise DeploymentContractError("foreign pinned deployment input residue blocks rotation")
        if archived_v2_predecessor_pinset is None:
            _verify_legacy_v1_predecessor(
                environment=environment,
                raw=env_bytes,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
                runner=runner,
            )
        if archived_v2_predecessor_pinset is None:
            _require_prior_cache_target_state_terminal_and_archive_f1d(environment)
        rendered = _render_v2_env(env_bytes, paths=paths, specs=specs)
        journal = _initial_journal(
            raw=env_bytes,
            rendered=rendered,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
            old_pinset_sha256=(
                legacy_predecessor_pinset_sha256()
                if archived_v2_predecessor_pinset is None
                else archived_v2_predecessor_pinset
            ),
        )
        _ensure_private_backup(paths.backup, env_bytes)
        _write_journal(paths.journal, journal)

    try:
        materialization_paths = PinnedSourceInstallPaths(
            state_directory=paths.state_directory,
            journal=paths.journal,
            backup=paths.backup,
            sources_directory=paths.sources_directory,
        )
        trees = {
            spec.label: _materialize_pinned_worktree(
                paths=materialization_paths, spec=spec, runner=runner
            )
            for spec in specs
        }
        journal = {**journal, "repositories": _repository_evidence(paths, specs, trees)}
        _write_journal(paths.journal, journal)
        _verify_pinned_artifacts(paths=paths, specs=specs, runner=runner)
        if journal["phase"] == "prepared":
            rendered = _render_v2_env(env_bytes, paths=paths, specs=specs)
            if hashlib.sha256(rendered).hexdigest() != journal["new_env_sha256"]:
                raise DeploymentContractError(
                    "pinned deployment input rendered env differs from journal"
                )
            replace_canonical_env_file(
                env_path,
                expected_sha256=journal["old_env_sha256"],
                replacement=rendered,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
            )
            journal = _transition(journal, "env_replaced")
            _write_journal(paths.journal, journal)
        _verify_journal(
            paths=paths,
            journal=journal,
            env_path=env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
            specs=specs,
            runner=runner,
        )
        journal = _transition(journal, "handoff_pending")
        _write_journal(paths.journal, journal)
    except Exception:
        if journal["phase"] in {"prepared", "env_replaced"}:
            _restore_predecessor_env(
                paths=paths,
                journal=journal,
                env_path=env_path,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
            )
            journal = _transition(journal, "rollback_preparing")
            journal = _transition(journal, "rolled_back")
            _write_journal(paths.journal, journal)
        raise
    return {"success": True, "state": "handoff_pending", "resumed": False}


def require_pinned_deployment_input_handoff(
    *,
    environment: Mapping[str, str],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner,
) -> dict[str, Any]:
    """F1D만 handoff pending input을 소비하게 한다."""

    paths = pinned_deployment_input_paths(environment)
    if not _path_exists_lstat(paths.journal):
        raise DeploymentContractError(
            "pinned drift bootstrap requires a v2 deployment input handoff"
        )
    journal = _read_journal(paths.journal)
    specs = pinned_deployment_repo_specs()
    _verify_journal(
        paths=paths,
        journal=journal,
        env_path=env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
        specs=specs,
        runner=runner,
    )
    if journal["phase"] not in {"handoff_pending", "f1d_in_progress", "f1d_completed"}:
        raise DeploymentContractError(
            "pinned drift bootstrap requires a v2 deployment input handoff receipt"
        )
    return journal


def mark_pinned_deployment_input_f1d_started(values: Mapping[str, str]) -> None:
    paths = pinned_deployment_input_paths(values)
    journal = _read_journal(paths.journal)
    if journal["phase"] == "handoff_pending":
        _write_journal(paths.journal, _transition(journal, "f1d_in_progress"))
    elif journal["phase"] == "f1d_completed":
        return
    elif journal["phase"] != "f1d_in_progress":
        raise DeploymentContractError("pinned deployment input cannot start F1D")


def mark_pinned_deployment_input_f1d_completed(values: Mapping[str, str]) -> None:
    paths = pinned_deployment_input_paths(values)
    journal = _read_journal(paths.journal)
    if journal["phase"] == "f1d_completed":
        return
    if journal["phase"] != "f1d_in_progress":
        raise DeploymentContractError("pinned deployment input cannot complete F1D")
    _write_journal(paths.journal, _transition(journal, "f1d_completed"))


def assert_pinned_deployment_input_allows_pair_mutation(
    *,
    environment: Mapping[str, str],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner,
) -> None:
    """handoff나 crash residue를 일반 pair operation이 덮어쓰지 못하게 한다."""

    paths = pinned_deployment_input_paths(environment)
    if not _path_exists_lstat(paths.journal):
        if _path_exists_lstat(paths.backup) or _path_exists_lstat(paths.sources_directory):
            raise DeploymentContractError(
                "foreign pinned deployment input residue blocks pair mutation"
            )
        return
    journal = _read_journal(paths.journal)
    _verify_journal(
        paths=paths,
        journal=journal,
        env_path=env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
        specs=pinned_deployment_repo_specs(),
        runner=runner,
    )
    if journal["phase"] not in _TERMINAL_PAIR_PHASES:
        raise DeploymentContractError("pinned deployment input handoff blocks pair mutation")


def _render_v2_env(
    raw: bytes, *, paths: PinnedDeploymentInputPaths, specs: Sequence[RepoSpec]
) -> bytes:
    if not raw or len(raw) > _MAX_ENV_BYTES:
        raise DeploymentContractError("canonical env size is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical env must be UTF-8") from exc
    pins = CACHE_TARGET_PRODUCTION_PINS
    target_roots = {
        spec.label: paths.sources_directory / spec.label / spec.revision for spec in specs
    }
    values = {
        **{spec.source_key: str(target_roots[spec.label]) for spec in specs},
        **{spec.revision_key: spec.revision for spec in specs},
        "KOR_TRAVEL_MAP_MIGRATION_EXPECTED_HEAD": pins.map_application_alembic_head,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256": pins.service_openapi_sha256,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION": pins.map_release_revision,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION": pins.contract_generation,
    }
    found: set[str] = set()
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        matched = _DOTENV.fullmatch(body)
        if matched is None or matched.group(1) not in values:
            output.append(line)
            continue
        key = matched.group(1)
        if key in found:
            raise DeploymentContractError(
                "pinned deployment input dotenv key must occur exactly once"
            )
        found.add(key)
        output.append(f"{key}={values[key]}{ending}")
    if len(found) != len(values):
        if output and not output[-1].endswith(("\n", "\r")):
            output.append("\n")
        output.extend(f"{key}={value}\n" for key, value in values.items() if key not in found)
    rendered = "".join(output).encode("utf-8")
    if rendered == raw:
        raise DeploymentContractError("pinned deployment input must change canonical env")
    return rendered


def _initial_journal(
    *,
    raw: bytes,
    rendered: bytes,
    expected_owner_uid: int,
    expected_owner_gid: int,
    old_pinset_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "version": 2,
        "phase": "prepared",
        "old_pinset_sha256": (
            legacy_predecessor_pinset_sha256()
            if old_pinset_sha256 is None
            else old_pinset_sha256
        ),
        "new_pinset_sha256": production_pinset_sha256(),
        "old_env_sha256": hashlib.sha256(raw).hexdigest(),
        "new_env_sha256": hashlib.sha256(rendered).hexdigest(),
        "backup_sha256": hashlib.sha256(raw).hexdigest(),
        "env_uid": expected_owner_uid,
        "env_gid": expected_owner_gid,
        "repositories": [],
    }


def _repository_evidence(
    paths: PinnedDeploymentInputPaths,
    specs: Sequence[RepoSpec],
    trees: Mapping[str, str],
) -> list[dict[str, str]]:
    return [
        {
            "label": spec.label,
            "revision": spec.revision,
            "target_root": str(paths.sources_directory / spec.label / spec.revision),
            "tree": trees[spec.label],
        }
        for spec in specs
    ]


def _read_journal(path: Path) -> dict[str, Any]:
    raw = _read_private_file(
        path, label="pinned deployment input journal", maximum_bytes=_MAX_JOURNAL_BYTES
    )
    try:
        journal = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentContractError("pinned deployment input journal is invalid") from exc
    if not isinstance(journal, dict):
        raise DeploymentContractError("pinned deployment input journal is invalid")
    _validate_journal(journal)
    return journal


def _write_journal(path: Path, journal: Mapping[str, Any]) -> None:
    _validate_journal(journal)
    _ensure_root_directory(path.parent, mode=0o700)
    payload = json.dumps(journal, separators=(",", ":"), sort_keys=True).encode("utf-8")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _read_private_file(
            path, label="pinned deployment input journal", maximum_bytes=_MAX_JOURNAL_BYTES
        )
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError("pinned deployment input journal cannot be written") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_journal(journal: Mapping[str, Any]) -> None:
    expected = {
        "version",
        "phase",
        "old_pinset_sha256",
        "new_pinset_sha256",
        "old_env_sha256",
        "new_env_sha256",
        "backup_sha256",
        "env_uid",
        "env_gid",
        "repositories",
    }
    if (
        set(journal) != expected
        or journal.get("version") != 2
        or journal.get("phase") not in _PHASES
    ):
        raise DeploymentContractError("pinned deployment input journal schema is invalid")
    for key in (
        "old_pinset_sha256",
        "new_pinset_sha256",
        "old_env_sha256",
        "new_env_sha256",
        "backup_sha256",
    ):
        if not isinstance(journal.get(key), str) or _SHA256.fullmatch(journal[key]) is None:
            raise DeploymentContractError("pinned deployment input journal digest is invalid")
    if journal["old_env_sha256"] == journal["new_env_sha256"]:
        raise DeploymentContractError("pinned deployment input journal env transition is invalid")
    if type(journal.get("env_uid")) is not int or type(journal.get("env_gid")) is not int:
        raise DeploymentContractError("pinned deployment input journal owner is invalid")
    repositories = journal["repositories"]
    if not isinstance(repositories, list) or len(repositories) not in {0, 2}:
        raise DeploymentContractError(
            "pinned deployment input journal repository evidence is invalid"
        )
    labels: set[str] = set()
    for entry in repositories:
        if not isinstance(entry, dict) or set(entry) != {
            "label",
            "revision",
            "target_root",
            "tree",
        }:
            raise DeploymentContractError(
                "pinned deployment input journal repository evidence is invalid"
            )
        if entry["label"] not in {"map", "pinvi"} or entry["label"] in labels:
            raise DeploymentContractError(
                "pinned deployment input journal repository label is invalid"
            )
        labels.add(entry["label"])
        if not all(isinstance(entry[key], str) for key in ("revision", "target_root", "tree")):
            raise DeploymentContractError(
                "pinned deployment input journal repository value is invalid"
            )
        if (
            _REVISION.fullmatch(entry["revision"]) is None
            or _REVISION.fullmatch(entry["tree"]) is None
        ):
            raise DeploymentContractError(
                "pinned deployment input journal repository value is invalid"
            )
    if repositories and labels != {"map", "pinvi"}:
        raise DeploymentContractError(
            "pinned deployment input journal repository labels are incomplete"
        )


def _transition(
    journal: Mapping[str, Any],
    phase: Literal[
        "env_replaced",
        "handoff_pending",
        "f1d_in_progress",
        "f1d_completed",
        "rollback_preparing",
        "rolled_back",
    ],
) -> dict[str, Any]:
    allowed = {
        "prepared": {"env_replaced", "rollback_preparing"},
        "env_replaced": {"handoff_pending", "rollback_preparing"},
        "handoff_pending": {"f1d_in_progress"},
        "f1d_in_progress": {"f1d_completed"},
        "rollback_preparing": {"rolled_back"},
        "f1d_completed": set(),
        "rolled_back": set(),
    }
    if phase not in allowed.get(journal["phase"], set()):
        raise DeploymentContractError("pinned deployment input journal transition is invalid")
    updated = {**journal, "phase": phase}
    _validate_journal(updated)
    return updated


def _verify_journal(
    *,
    paths: PinnedDeploymentInputPaths,
    journal: Mapping[str, Any],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
    specs: Sequence[RepoSpec],
    runner: GitRunner,
) -> None:
    _validate_journal(journal)
    if journal["new_pinset_sha256"] != production_pinset_sha256():
        raise DeploymentContractError(
            "pinned deployment input pinset differs from current manifest"
        )
    if journal["env_uid"] != expected_owner_uid or journal["env_gid"] != expected_owner_gid:
        raise DeploymentContractError("pinned deployment input env owner differs from frozen env")
    backup = _read_private_file(
        paths.backup, label="pinned deployment input backup", maximum_bytes=_MAX_ENV_BYTES
    )
    if hashlib.sha256(backup).hexdigest() != journal["backup_sha256"]:
        raise DeploymentContractError("pinned deployment input backup differs from journal")
    current = read_canonical_env_file(
        env_path, expected_owner_uid=expected_owner_uid, expected_owner_gid=expected_owner_gid
    )
    current_sha256 = hashlib.sha256(current).hexdigest()
    expected_env_sha256 = (
        journal["old_env_sha256"]
        if journal["phase"] in {"prepared", "rollback_preparing", "rolled_back"}
        else journal["new_env_sha256"]
    )
    allowed_env_sha256 = {expected_env_sha256}
    if journal["phase"] == "prepared" and journal["repositories"]:
        # env replace 직후 receipt write 전에 죽은 유일한 crash window는 exact target
        # digest와 이미 검증된 worktree evidence가 함께 있을 때만 재개한다.
        allowed_env_sha256.add(journal["new_env_sha256"])
    if current_sha256 not in allowed_env_sha256:
        raise DeploymentContractError("pinned deployment input canonical env differs from journal")
    if not journal["repositories"]:
        if journal["phase"] not in {"prepared", "rollback_preparing", "rolled_back"}:
            raise DeploymentContractError(
                "pinned deployment input lacks materialized repository evidence"
            )
        return
    by_label = {entry["label"]: entry for entry in journal["repositories"]}
    for spec in specs:
        entry = by_label.get(spec.label)
        target = paths.sources_directory / spec.label / spec.revision
        if (
            entry is None
            or entry["revision"] != spec.revision
            or Path(entry["target_root"]) != target
        ):
            raise DeploymentContractError("pinned deployment input repository evidence drifted")
        tree = _validate_existing_pinned_worktree(target, spec=spec, runner=runner)
        if tree != entry["tree"]:
            raise DeploymentContractError("pinned deployment input worktree tree drifted")
    _verify_pinned_artifacts(paths=paths, specs=specs, runner=runner)


def _prepared_env_replacement_needs_recovery(
    *,
    journal: Mapping[str, Any],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> bool:
    if journal["phase"] != "prepared":
        return False
    current = read_canonical_env_file(
        env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    return hashlib.sha256(current).hexdigest() == str(journal["new_env_sha256"])


def _archive_completed_v2_predecessor(
    *,
    paths: PinnedDeploymentInputPaths,
    journal: Mapping[str, Any],
    environment: Mapping[str, str],
    env_bytes: bytes,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner,
) -> str:
    """다음 pinset을 열기 전 immutable v2 input의 F1D receipt를 archive한다."""

    _validate_journal(journal)
    if journal["phase"] != "f1d_completed" or not journal["repositories"]:
        raise DeploymentContractError(
            "unfinished pinned deployment input blocks next pinset rotation"
        )
    predecessor_pinset = str(journal["new_pinset_sha256"])
    if hashlib.sha256(env_bytes).hexdigest() != journal["new_env_sha256"]:
        raise DeploymentContractError(
            "terminal pinned deployment input canonical env differs from receipt"
        )
    backup = _read_private_file(
        paths.backup,
        label="pinned deployment input backup",
        maximum_bytes=_MAX_ENV_BYTES,
    )
    if hashlib.sha256(backup).hexdigest() != journal["backup_sha256"]:
        raise DeploymentContractError("terminal pinned deployment input backup differs from receipt")
    historical_specs = _historical_repo_specs(journal)
    for spec in historical_specs:
        entry = next(entry for entry in journal["repositories"] if entry["label"] == spec.label)
        target = paths.sources_directory / spec.label / spec.revision
        if Path(entry["target_root"]) != target:
            raise DeploymentContractError("terminal pinned deployment input target escaped state")
        if _validate_existing_pinned_worktree(target, spec=spec, runner=runner) != entry["tree"]:
            raise DeploymentContractError("terminal pinned deployment input worktree drifted")

    _require_prior_cache_target_state_terminal_and_archive_f1d(
        environment,
        prior_v2_pinset_sha256=predecessor_pinset,
        prior_input_env_sha256=str(journal["new_env_sha256"]),
    )
    return predecessor_pinset


def _find_terminal_v2_predecessor(
    *, paths: PinnedDeploymentInputPaths, env_bytes: bytes
) -> tuple[PinnedDeploymentInputPaths, dict[str, Any]] | None:
    """현재 canonical env와 결합된 하나의 prior generation만 rotation에 수용한다."""

    try:
        history_metadata = paths.history_directory.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DeploymentContractError("pinned deployment input history is unavailable") from exc
    if (
        not stat.S_ISDIR(history_metadata.st_mode)
        or stat.S_ISLNK(history_metadata.st_mode)
        or history_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(history_metadata.st_mode) != 0o700
    ):
        raise DeploymentContractError("pinned deployment input history is unsafe")
    try:
        entries = tuple(paths.history_directory.iterdir())
    except OSError as exc:
        raise DeploymentContractError("pinned deployment input history is unavailable") from exc
    matches: list[tuple[PinnedDeploymentInputPaths, dict[str, Any]]] = []
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise DeploymentContractError("pinned deployment input history is unsafe") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or _SHA256.fullmatch(entry.name) is None
        ):
            raise DeploymentContractError("pinned deployment input history is unsafe")
        predecessor_paths = PinnedDeploymentInputPaths(
            state_directory=paths.state_directory,
            generation_directory=entry,
            journal=entry / _JOURNAL_NAME,
            backup=entry / _BACKUP_NAME,
            sources_directory=paths.sources_directory,
            history_directory=paths.history_directory,
        )
        if not _path_exists_lstat(predecessor_paths.journal):
            if _path_exists_lstat(predecessor_paths.backup):
                raise DeploymentContractError("pinned deployment input history residue is invalid")
            continue
        journal = _read_journal(predecessor_paths.journal)
        if journal["new_pinset_sha256"] != entry.name:
            raise DeploymentContractError("pinned deployment input history pinset path drifted")
        if journal["phase"] not in _TERMINAL_PAIR_PHASES:
            raise DeploymentContractError("unfinished historic deployment input blocks rotation")
        if (
            journal["phase"] == "f1d_completed"
            and hashlib.sha256(env_bytes).hexdigest() == journal["new_env_sha256"]
        ):
            matches.append((predecessor_paths, journal))
    if len(matches) > 1:
        raise DeploymentContractError("canonical env matches multiple deployment input histories")
    return matches[0] if matches else None


def _verify_rolled_back_predecessor(
    *,
    paths: PinnedDeploymentInputPaths,
    journal: Mapping[str, Any],
    environment: Mapping[str, str],
    raw: bytes,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner,
) -> None:
    """rollback된 generation은 그 receipt가 기록한 predecessor로만 재시도한다."""

    predecessor_pinset = str(journal["old_pinset_sha256"])
    if predecessor_pinset == legacy_predecessor_pinset_sha256():
        _verify_legacy_v1_predecessor(
            environment=environment,
            raw=raw,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
            runner=runner,
        )
        _require_prior_cache_target_state_terminal_and_archive_f1d(environment)
        return
    predecessor = _find_terminal_v2_predecessor(paths=paths, env_bytes=raw)
    if predecessor is None or predecessor[1]["new_pinset_sha256"] != predecessor_pinset:
        raise DeploymentContractError(
            "rolled-back pinned deployment input predecessor differs from receipt"
        )
    _archive_completed_v2_predecessor(
        paths=predecessor[0],
        journal=predecessor[1],
        environment=environment,
        env_bytes=raw,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
        runner=runner,
    )


def _historical_repo_specs(journal: Mapping[str, Any]) -> tuple[RepoSpec, RepoSpec]:
    entries = journal["repositories"]
    if not isinstance(entries, list):
        raise DeploymentContractError("pinned deployment input history repository evidence is invalid")
    by_label = {entry["label"]: entry for entry in entries if isinstance(entry, dict)}
    definitions = {
        "map": ("KOR_TRAVEL_MAP_REPO_DIR", "KOR_TRAVEL_MAP_GIT_COMMIT", "https://github.com/digitie/kor-travel-map.git"),
        "pinvi": ("PINVI_REPO_DIR", "PINVI_SOURCE_REVISION", "https://github.com/digitie/pinvi.git"),
    }
    try:
        return tuple(
            RepoSpec(label, source_key, revision_key, url, str(by_label[label]["revision"]))
            for label, (source_key, revision_key, url) in definitions.items()
        )  # type: ignore[return-value]
    except (KeyError, TypeError) as exc:
        raise DeploymentContractError(
            "pinned deployment input history repository evidence is invalid"
        ) from exc


def _ensure_private_backup(path: Path, raw: bytes) -> None:
    if _path_exists_lstat(path):
        existing = _read_private_file(
            path,
            label="pinned deployment input backup",
            maximum_bytes=_MAX_ENV_BYTES,
        )
        if existing != raw:
            raise DeploymentContractError("pinned deployment input backup conflicts with retry")
        return
    _write_private_backup(path, raw)


def _restore_predecessor_env(
    *,
    paths: PinnedDeploymentInputPaths,
    journal: Mapping[str, Any],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> None:
    current = read_canonical_env_file(
        env_path, expected_owner_uid=expected_owner_uid, expected_owner_gid=expected_owner_gid
    )
    if hashlib.sha256(current).hexdigest() == journal["new_env_sha256"]:
        backup = _read_private_file(
            paths.backup, label="pinned deployment input backup", maximum_bytes=_MAX_ENV_BYTES
        )
        replace_canonical_env_file(
            env_path,
            expected_sha256=journal["new_env_sha256"],
            replacement=backup,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )


def _verify_pinned_artifacts(
    *, paths: PinnedDeploymentInputPaths, specs: Sequence[RepoSpec], runner: GitRunner
) -> None:
    pins = CACHE_TARGET_PRODUCTION_PINS
    by_label = {spec.label: spec for spec in specs}
    map_root = paths.sources_directory / "map" / by_label["map"].revision
    pinvi_root = paths.sources_directory / "pinvi" / by_label["pinvi"].revision
    map_service = map_root / "packages/kor-travel-map-api/openapi.service.json"
    pinvi_service = pinvi_root / "apps/api/tests/contract/kor-travel-map-openapi-service.json"
    provenance_path = pinvi_root / "contracts/kor-travel-map-service-provenance-v1.json"
    for path, label in (
        (map_service, "Map service artifact"),
        (pinvi_service, "PinVi service vendor"),
        (provenance_path, "PinVi Map service provenance"),
    ):
        _require_root_owned_file(path, label=label)
    map_bytes = map_service.read_bytes()
    pinvi_bytes = pinvi_service.read_bytes()
    if (
        hashlib.sha256(map_bytes).hexdigest() != pins.service_openapi_sha256
        or pinvi_bytes != map_bytes
    ):
        raise DeploymentContractError("pinned Map and PinVi service artifacts differ from manifest")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("pinned PinVi Map service provenance is invalid") from exc
    expected = {
        "capabilities": {
            "cache_target": {"generation": int(pins.contract_generation)},
            "c6c_cancel_probe": {"generation": C6C_CANCEL_PROBE_CAPABILITY_GENERATION},
        },
        "map_release_revision": pins.map_release_revision,
        "service_openapi_sha256": pins.service_openapi_sha256,
        "version": 1,
    }
    if provenance != expected:
        raise DeploymentContractError("pinned PinVi Map service provenance differs from manifest")
    # immutable worktree evidence를 다시 읽어 artifact path가 worktree 밖으로 escape하지 않음을 보장한다.
    _validate_existing_pinned_worktree(map_root, spec=by_label["map"], runner=runner)
    _validate_existing_pinned_worktree(pinvi_root, spec=by_label["pinvi"], runner=runner)


def _require_root_owned_file(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _verify_legacy_v1_predecessor(
    *,
    environment: Mapping[str, str],
    raw: bytes,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner,
) -> None:
    """유일하게 허용된 v1 terminal receipt만 v2 첫 rotation의 predecessor로 받는다."""

    manifest_path, _ = c6c_state_paths(environment)
    state_directory = Path(manifest_path).parent
    journal_path = state_directory / "pinned-source-install-v1.json"
    backup_path = state_directory / "pinned-source-install-v1.env.backup"
    source_directory = state_directory / "pinned-sources-v1"
    legacy_raw = _read_private_file(
        journal_path, label="legacy pinned source journal", maximum_bytes=_MAX_JOURNAL_BYTES
    )
    legacy_backup = _read_private_file(
        backup_path, label="legacy pinned source backup", maximum_bytes=_MAX_ENV_BYTES
    )
    try:
        journal = json.loads(legacy_raw)
    except json.JSONDecodeError as exc:
        raise DeploymentContractError("legacy pinned source journal is invalid") from exc
    expected_keys = {
        "version",
        "phase",
        "old_env_sha256",
        "new_env_sha256",
        "backup_sha256",
        "env_uid",
        "env_gid",
        "repositories",
    }
    if (
        not isinstance(journal, dict)
        or set(journal) != expected_keys
        or journal.get("version") != 1
        or journal.get("phase") != "committed"
    ):
        raise DeploymentContractError(
            "v2 rotation requires a terminal legacy pinned source receipt"
        )
    if journal.get("env_uid") != expected_owner_uid or journal.get("env_gid") != expected_owner_gid:
        raise DeploymentContractError(
            "legacy pinned source receipt owner differs from canonical env"
        )
    if hashlib.sha256(legacy_backup).hexdigest() != journal.get("backup_sha256"):
        raise DeploymentContractError("legacy pinned source backup differs from receipt")
    if hashlib.sha256(raw).hexdigest() != journal.get("new_env_sha256"):
        raise DeploymentContractError(
            "v2 rotation requires the exact legacy canonical env predecessor"
        )
    values = _dotenv_values(raw)
    expected_values = {
        "KOR_TRAVEL_MAP_REPO_DIR": str(source_directory / "map" / _LEGACY_MAP_REVISION),
        "KOR_TRAVEL_MAP_GIT_COMMIT": _LEGACY_MAP_REVISION,
        "PINVI_REPO_DIR": str(source_directory / "pinvi" / _LEGACY_PINVI_REVISION),
        "PINVI_SOURCE_REVISION": _LEGACY_PINVI_REVISION,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_OPENAPI_SHA256": _LEGACY_SERVICE_SHA256,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_SOURCE_REVISION": _LEGACY_MAP_SOURCE_REVISION,
        "PINVI_KOR_TRAVEL_MAP_CACHE_TARGET_EXPECTED_CONTRACT_GENERATION": _LEGACY_CONTRACT_GENERATION,
    }
    if any(values.get(key) != value for key, value in expected_values.items()):
        raise DeploymentContractError(
            "legacy canonical env is not the approved v1 predecessor pinset"
        )
    specs = (
        RepoSpec(
            "map",
            "KOR_TRAVEL_MAP_REPO_DIR",
            "KOR_TRAVEL_MAP_GIT_COMMIT",
            "https://github.com/digitie/kor-travel-map.git",
            _LEGACY_MAP_REVISION,
        ),
        RepoSpec(
            "pinvi",
            "PINVI_REPO_DIR",
            "PINVI_SOURCE_REVISION",
            "https://github.com/digitie/pinvi.git",
            _LEGACY_PINVI_REVISION,
        ),
    )
    entries = journal.get("repositories")
    if not isinstance(entries, list) or len(entries) != 2:
        raise DeploymentContractError("legacy pinned source receipt repository evidence is invalid")
    by_label = {entry.get("label"): entry for entry in entries if isinstance(entry, dict)}
    for spec in specs:
        entry = by_label.get(spec.label)
        target = source_directory / spec.label / spec.revision
        if (
            not isinstance(entry, dict)
            or entry.get("revision") != spec.revision
            or entry.get("target_root") != str(target)
        ):
            raise DeploymentContractError(
                "legacy pinned source receipt repository differs from predecessor"
            )
        tree = _validate_existing_pinned_worktree(target, spec=spec, runner=runner)
        if tree != entry.get("tree"):
            raise DeploymentContractError(
                "legacy pinned source worktree differs from predecessor receipt"
            )


def _require_prior_cache_target_state_terminal_and_archive_f1d(
    environment: Mapping[str, str],
    *,
    prior_v2_pinset_sha256: str | None = None,
    prior_input_env_sha256: str | None = None,
) -> None:
    """v2 rotation 전에 기존 cutover durable state를 모두 terminal로 고정한다.

    writer-drain은 standalone receipt를 만들지 않고 window/diagnostic journal의
    evidence로만 남는다. 각 typed reader가 해당 receipt의 복구/terminal invariant도
    함께 검증하므로 별도 파일을 추측해서 읽지 않는다.
    """

    manifest_path, _ = c6c_state_paths(environment)
    state_directory = Path(manifest_path).parent
    window_path = state_directory / "cache-target-window-v1.json"
    diagnostic_path = state_directory / "cache-target-diagnostic-v1.json"
    enable_path = state_directory / "cache-target-enable-v1.json"

    if _path_exists_lstat(window_path):
        window = read_cache_target_window(window_path)
        if window.phase not in WINDOW_TERMINAL_PHASES:
            raise DeploymentContractError(
                "unfinished cache-target window blocks v2 input rotation"
            )
    if _path_exists_lstat(diagnostic_path):
        diagnostic = read_cache_target_diagnostic(diagnostic_path)
        if diagnostic.phase not in DIAGNOSTIC_TERMINAL_PHASES:
            raise DeploymentContractError(
                "unfinished cache-target diagnostic blocks v2 input rotation"
            )
    if _path_exists_lstat(enable_path):
        enable = read_enable_cutover_journal(enable_path)
        if enable.phase not in {"committed", "rolled_back"}:
            raise DeploymentContractError(
                "unfinished cache-target enable blocks v2 input rotation"
            )
    archive_terminal_legacy_pinned_drift_bootstrap(environment)
    if prior_v2_pinset_sha256 is not None:
        archived = archive_terminal_pinned_drift_bootstrap(
            environment,
            pinset_sha256=prior_v2_pinset_sha256,
        )
        if archived is None:
            raise DeploymentContractError(
                "terminal pinned deployment input is missing its F1D receipt"
            )
        f1d = read_pinned_drift_bootstrap(archived)
        if f1d is None or f1d.environment_sha256 != prior_input_env_sha256:
            raise DeploymentContractError(
                "terminal pinned deployment input differs from its F1D frozen environment"
            )


def _dotenv_values(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical env must be UTF-8") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        matched = _DOTENV.fullmatch(line)
        if matched is None:
            continue
        key, value = matched.groups()
        if key in values:
            raise DeploymentContractError("canonical env contains duplicate deployment input key")
        values[key] = value
    return values
