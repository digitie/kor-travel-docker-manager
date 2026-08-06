"""F1D v5 candidate build용 immutable source materialization.

이 모듈은 사람 소유 checkout을 *동의 증거*로만 읽는다. root Git은 그
checkout의 config, hook, remote를 절대로 사용하지 않고, tracked v5 release의
canonical HTTPS URL과 exact revision만 새 owner-only state namespace에 fetch한다.
기존 source tree나 canonical environment은 변경하지 않는다.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeStatePaths,
    ensure_pinned_runtime_state_directory,
)
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    pinned_runtime_state_paths as canonical_pinned_runtime_state_paths,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    RUNTIME_SOURCE_ROLES,
    PinnedRuntimeRelease,
    PinnedRuntimeSourceSpec,
    RuntimeSourceRole,
)

_SOURCES_DIRECTORY_NAME = "pinned-runtime-sources-v5"
_WORKTREES_DIRECTORY_NAME = "worktrees"
_STAGING_DIRECTORY_NAME = ".staging"
_BARE_DIRECTORY_NAME = "bare"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ROOT_KEYS: Mapping[RuntimeSourceRole, str] = MappingProxyType(
    {
        "map": "KOR_TRAVEL_MAP_REPO_DIR",
        "pinvi": "PINVI_REPO_DIR",
    }
)

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PinnedRuntimeSourcePaths:
    """v5 release pinset 하나가 독점하는 source staging 경로."""

    state_directory: Path
    pinset_directory: Path
    bare_directory: Path
    worktrees_directory: Path

    def bare_repository(self, role: RuntimeSourceRole) -> Path:
        return self.bare_directory / f"{role}.git"

    def worktree(self, source: PinnedRuntimeSourceSpec) -> Path:
        return self.worktrees_directory / source.role / source.revision

    def staging_directory(self, source: PinnedRuntimeSourceSpec) -> Path:
        return self.worktrees_directory / _STAGING_DIRECTORY_NAME / source.role

    def staging_worktree(self, source: PinnedRuntimeSourceSpec) -> Path:
        """아직 public source root가 아닌, 단일 시도 전용 worktree 경로."""

        return self.staging_directory(source) / f"{source.revision}-{uuid.uuid4().hex}"


@dataclass(frozen=True)
class MaterializedRuntimeSource:
    """후보 build가 소비할 source root와 immutable Git evidence."""

    role: RuntimeSourceRole
    root: Path
    revision: str
    tree: str

    def __post_init__(self) -> None:
        if _REVISION.fullmatch(self.revision) is None:
            raise DeploymentContractError("materialized runtime source revision is invalid")
        if _REVISION.fullmatch(self.tree) is None:
            raise DeploymentContractError("materialized runtime source tree is invalid")


@dataclass(frozen=True)
class PinnedRuntimeSourceMaterialization:
    """candidate build와 v5 journal이 공유하는 source pinset 결과."""

    release: PinnedRuntimeRelease
    sources: tuple[MaterializedRuntimeSource, ...]

    def __post_init__(self) -> None:
        sources = tuple(self.sources)
        object.__setattr__(self, "sources", sources)
        roles = tuple(source.role for source in sources)
        if roles != RUNTIME_SOURCE_ROLES:
            raise DeploymentContractError("materialized runtime source roles are incomplete")
        for source in sources:
            if source.revision != self.release.source_for(source.role).revision:
                raise DeploymentContractError("materialized runtime source revision differs from release")

    @property
    def pinset_sha256(self) -> str:
        return self.release.pinset_sha256

    @property
    def source_roots(self) -> Mapping[RuntimeSourceRole, Path]:
        return MappingProxyType({source.role: source.root for source in self.sources})

    def source_for(self, role: RuntimeSourceRole) -> MaterializedRuntimeSource:
        return self.sources[RUNTIME_SOURCE_ROLES.index(role)]


@dataclass(frozen=True)
class _SourceIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int


def pinned_runtime_source_paths(
    *,
    state_paths: PinnedRuntimeStatePaths,
    release: PinnedRuntimeRelease,
) -> PinnedRuntimeSourcePaths:
    """state root 아래 release digest로 격리된 source namespace를 만든다."""

    if _SHA256.fullmatch(release.pinset_sha256) is None:
        raise DeploymentContractError("pinned runtime source pinset digest is invalid")
    state_directory = state_paths.state_root / _SOURCES_DIRECTORY_NAME
    pinset_directory = state_directory / release.pinset_sha256
    return PinnedRuntimeSourcePaths(
        state_directory=state_directory,
        pinset_directory=pinset_directory,
        bare_directory=pinset_directory / _BARE_DIRECTORY_NAME,
        worktrees_directory=pinset_directory / _WORKTREES_DIRECTORY_NAME,
    )


def materialize_pinned_runtime_sources(
    *,
    release: PinnedRuntimeRelease,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
    runner: GitRunner = subprocess.run,
) -> PinnedRuntimeSourceMaterialization:
    """canonical source consent을 확인하고 exact v5 sources를 materialize한다.

    source root의 local origin만 source owner 권한으로 읽는다. 그 뒤 모든 Git
    명령은 root-owned state repository에서 canonical HTTPS URL과 exact release
    revision만 사용한다.
    """

    _require_canonical_rebuildable_state_paths(state_paths=state_paths, values=values)
    paths = pinned_runtime_source_paths(state_paths=state_paths, release=release)
    source_roots = {
        source.role: _validated_source_root(values, source=source)
        for source in release.sources
    }
    for source in release.sources:
        _verify_source_origin(source_roots[source.role], source=source, runner=runner)

    ensure_pinned_runtime_state_directory(state_paths.state_root)
    _ensure_private_directory(paths.state_directory)
    _ensure_private_directory(paths.pinset_directory)
    _ensure_private_directory(paths.bare_directory)
    _ensure_private_directory(paths.worktrees_directory)

    materialized = tuple(
        _materialize_source(paths=paths, source=source, runner=runner)
        for source in release.sources
    )
    return PinnedRuntimeSourceMaterialization(release=release, sources=materialized)


def _require_canonical_rebuildable_state_paths(
    *,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
) -> None:
    """호출자가 v5 rebuild state 밖으로 source를 유도하지 못하게 막는다."""

    expected = canonical_pinned_runtime_state_paths(values)
    if state_paths != expected:
        raise DeploymentContractError(
            "pinned runtime source state paths differ from canonical rebuildable state"
        )


def _validated_source_root(
    values: Mapping[str, str],
    *,
    source: PinnedRuntimeSourceSpec,
) -> Path:
    key = _SOURCE_ROOT_KEYS[source.role]
    value = values.get(key, "")
    if not isinstance(value, str) or not value or value != value.strip() or "$" in value or "`" in value:
        raise DeploymentContractError("pinned runtime source root is blank or interpolated")
    root = Path(value)
    if (
        not root.is_absolute()
        or value != os.path.abspath(value)
        or any(part in {"", ".", ".."} for part in root.parts[1:])
    ):
        raise DeploymentContractError("pinned runtime source root must be canonical absolute")
    return root


def _source_identity(root: Path, *, source: PinnedRuntimeSourceSpec) -> _SourceIdentity:
    _reject_symlink_components(root, label="pinned runtime source root")
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise DeploymentContractError("pinned runtime source root cannot be inspected") from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid == 0
        or root_stat.st_mode & 0o022
    ):
        raise DeploymentContractError("pinned runtime source root is unsafe")
    return _SourceIdentity(
        device=root_stat.st_dev,
        inode=root_stat.st_ino,
        uid=root_stat.st_uid,
        gid=root_stat.st_gid,
        mode=stat.S_IMODE(root_stat.st_mode),
    )


def _verify_source_origin(
    root: Path,
    *,
    source: PinnedRuntimeSourceSpec,
    runner: GitRunner,
) -> _SourceIdentity:
    """source-owner local config read가 canonical origin만 가리키는지 검증한다."""

    before = _source_identity(root, source=source)
    completed = runner(
        ["/usr/bin/git", "-C", str(root), "config", "--local", "--get", "remote.origin.url"],
        check=False,
        text=True,
        capture_output=True,
        cwd="/",
        env=_source_owner_git_environment(),
        preexec_fn=_drop_privileges(before.uid, before.gid),
    )
    if completed.returncode != 0:
        raise DeploymentContractError("pinned runtime source origin cannot be read as source owner")
    if completed.stdout != f"{source.canonical_url}\n":
        raise DeploymentContractError("pinned runtime source origin is not the canonical HTTPS URL")
    after = _source_identity(root, source=source)
    if after != before:
        raise DeploymentContractError("pinned runtime source root changed during origin verification")
    return before


def _materialize_source(
    *,
    paths: PinnedRuntimeSourcePaths,
    source: PinnedRuntimeSourceSpec,
    runner: GitRunner,
) -> MaterializedRuntimeSource:
    bare = paths.bare_repository(source.role)
    target = paths.worktree(source)
    _ensure_private_directory(target.parent)
    if _path_exists(bare):
        _validate_private_directory(bare, label="pinned runtime bare source")
    else:
        _run_root_git(["init", "--bare", str(bare)], runner=runner)
        try:
            os.chmod(bare, 0o700)
        except OSError as exc:
            raise DeploymentContractError("pinned runtime bare source cannot be secured") from exc
        _validate_private_directory(bare, label="pinned runtime bare source")

    if _path_exists(target):
        return _validate_existing_worktree(target=target, source=source, runner=runner)

    _run_root_git(
        ["--git-dir", str(bare), "fetch", "--no-tags", source.canonical_url, source.revision],
        runner=runner,
    )
    _run_root_git(
        ["--git-dir", str(bare), "cat-file", "-e", f"{source.revision}^{{commit}}"],
        runner=runner,
    )
    _assert_no_submodules(bare=bare, source=source, runner=runner)
    tree = _revision_output(
        _run_root_git(
            ["--git-dir", str(bare), "rev-parse", f"{source.revision}^{{tree}}"],
            runner=runner,
        ).stdout,
        label="pinned runtime source tree",
    )
    staging = paths.staging_worktree(source)
    _ensure_private_directory(staging.parent)
    try:
        _run_root_git(
            ["--git-dir", str(bare), "worktree", "add", "--detach", str(staging), source.revision],
            runner=runner,
        )
        _validate_private_staging_worktree(staging)
        try:
            # Git은 private parent 아래에도 새 worktree root를 보통 0755로
            # 만든다. parent가 이미 root-only임을 검증한 뒤 즉시 0700으로
            # 좁혀 이후 clean/seal/promotion 구간을 private하게 유지한다.
            os.chmod(staging, 0o700)
        except OSError as exc:
            raise DeploymentContractError(
                "pinned runtime source staging worktree cannot be secured"
            ) from exc
        _validate_private_staging_worktree(staging)
        _assert_worktree_clean(target=staging, runner=runner)
        _make_worktree_immutable(staging)
        materialized = _validate_existing_worktree(
            target=staging,
            source=source,
            runner=runner,
            expected_tree=tree,
        )
        _promote_staging_worktree(
            bare=bare,
            staging=staging,
            target=target,
            runner=runner,
        )
    except BaseException:
        _cleanup_staging_worktree(bare=bare, staging=staging, runner=runner)
        raise
    return MaterializedRuntimeSource(
        role=materialized.role,
        root=target,
        revision=materialized.revision,
        tree=materialized.tree,
    )


def _promote_staging_worktree(
    *,
    bare: Path,
    staging: Path,
    target: Path,
    runner: GitRunner,
) -> None:
    """seal 검증된 stage만 Git-owned move로 final source root에 공개한다."""

    if _path_exists(target):
        raise DeploymentContractError("pinned runtime source worktree target already exists")
    _run_root_git(
        ["--git-dir", str(bare), "worktree", "move", str(staging), str(target)],
        runner=runner,
    )


def _cleanup_staging_worktree(*, bare: Path, staging: Path, runner: GitRunner) -> None:
    """실패한 private stage만 제거한다. final target은 어떤 경우에도 건드리지 않는다."""

    if not _path_exists(staging):
        return
    _validate_private_staging_worktree(staging)
    try:
        _run_root_git(
            ["--git-dir", str(bare), "worktree", "remove", "--force", str(staging)],
            runner=runner,
        )
    except DeploymentContractError as exc:
        raise DeploymentContractError("pinned runtime source staging cleanup failed") from exc
    if _path_exists(staging):
        raise DeploymentContractError("pinned runtime source staging cleanup is incomplete")


def _validate_existing_worktree(
    *,
    target: Path,
    source: PinnedRuntimeSourceSpec,
    runner: GitRunner,
    expected_tree: str | None = None,
) -> MaterializedRuntimeSource:
    _validate_immutable_tree(target)
    revision = _revision_output(
        _run_root_git(
            ["-C", str(target), "rev-parse", "--verify", "HEAD"],
            runner=runner,
        ).stdout,
        label="pinned runtime source revision",
    )
    if revision != source.revision:
        raise DeploymentContractError("pinned runtime source worktree revision drifted")
    tree = _revision_output(
        _run_root_git(["-C", str(target), "rev-parse", "HEAD^{tree}"], runner=runner).stdout,
        label="pinned runtime source tree",
    )
    if expected_tree is not None and tree != expected_tree:
        raise DeploymentContractError("pinned runtime source worktree tree differs")
    _assert_worktree_clean(target=target, runner=runner)
    return MaterializedRuntimeSource(
        role=source.role,
        root=target,
        revision=revision,
        tree=tree,
    )


def _assert_no_submodules(
    *,
    bare: Path,
    source: PinnedRuntimeSourceSpec,
    runner: GitRunner,
) -> None:
    entries = _run_root_git(
        ["--git-dir", str(bare), "ls-tree", "-r", "--full-tree", source.revision],
        runner=runner,
    ).stdout.splitlines()
    if any(entry.startswith("160000 commit ") for entry in entries):
        raise DeploymentContractError("pinned runtime source repository must not contain submodules")


def _assert_worktree_clean(*, target: Path, runner: GitRunner) -> None:
    status = _run_root_git(
        ["-C", str(target), "status", "--porcelain=v1", "--untracked-files=normal"],
        runner=runner,
    ).stdout
    if status:
        raise DeploymentContractError("pinned runtime source worktree is not clean")


def _run_root_git(
    arguments: Sequence[str],
    *,
    runner: GitRunner,
) -> subprocess.CompletedProcess[str]:
    """HTTPS-only, config·hook·credential-free root Git invocation."""

    completed = runner(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "credential.helper=",
            *arguments,
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd="/",
        env=_root_git_environment(),
    )
    if completed.returncode != 0:
        raise DeploymentContractError("pinned runtime source staging Git operation failed")
    return completed


def _source_owner_git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
    }


def _root_git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
    }


def _drop_privileges(uid: int, gid: int) -> Callable[[], None]:
    def drop_privileges() -> None:
        os.setgroups([gid])
        os.setgid(gid)
        os.setuid(uid)

    return drop_privileges


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise DeploymentContractError("pinned runtime source state directory is unavailable") from exc
    _validate_private_directory(path, label="pinned runtime source state directory")


def _validate_private_directory(path: Path, *, label: str) -> None:
    _reject_symlink_components(path, label=label)
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISDIR(path_stat.st_mode)
        or path_stat.st_uid != os.geteuid()
        or stat.S_IMODE(path_stat.st_mode) != 0o700
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _validate_private_staging_worktree(path: Path) -> None:
    """정해진 private staging parent 안의 현재 owner worktree만 cleanup한다."""

    _validate_private_directory(path.parent, label="pinned runtime source staging directory")
    _reject_symlink_components(path, label="pinned runtime source staging worktree")
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(
            "pinned runtime source staging worktree cannot be inspected"
        ) from exc
    if (
        not stat.S_ISDIR(path_stat.st_mode)
        or path_stat.st_uid != os.geteuid()
        # Git default worktree root는 0755일 수 있다. parent는 이미 0700으로
        # 검증했으며, materialize 직후 즉시 0700으로 seal한다. cleanup은 seal
        # 이전 실패도 처리해야 하므로 이 transient mode만 추가로 허용한다.
        or stat.S_IMODE(path_stat.st_mode) not in {0o700, 0o755, 0o555}
    ):
        raise DeploymentContractError("pinned runtime source staging worktree is unsafe")


def _validate_immutable_tree(root: Path) -> None:
    _reject_symlink_components(root, label="pinned runtime source worktree")
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise DeploymentContractError("pinned runtime source worktree cannot be inspected") from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != os.geteuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o555
    ):
        raise DeploymentContractError("pinned runtime source worktree is unsafe")
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        current_stat = current_path.lstat()
        if (
            current_path.is_symlink()
            or not stat.S_ISDIR(current_stat.st_mode)
            or current_stat.st_uid != os.geteuid()
            or stat.S_IMODE(current_stat.st_mode) != 0o555
        ):
            raise DeploymentContractError("pinned runtime source worktree is unsafe")
        for name in directories:
            directory_path = current_path / name
            directory_stat = directory_path.lstat()
            if (
                directory_path.is_symlink()
                or not stat.S_ISDIR(directory_stat.st_mode)
                or directory_stat.st_uid != os.geteuid()
                or stat.S_IMODE(directory_stat.st_mode) != 0o555
            ):
                raise DeploymentContractError("pinned runtime source worktree is unsafe")
        for name in files:
            file_path = current_path / name
            file_stat = file_path.lstat()
            mode = stat.S_IMODE(file_stat.st_mode)
            if (
                file_path.is_symlink()
                or not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.geteuid()
                or file_stat.st_nlink != 1
                or mode not in {0o444, 0o555}
            ):
                raise DeploymentContractError("pinned runtime source worktree is unsafe")


def _make_worktree_immutable(root: Path) -> None:
    try:
        for current, directories, files in os.walk(root, topdown=False, followlinks=False):
            current_path = Path(current)
            if current_path.is_symlink():
                raise DeploymentContractError("pinned runtime source worktree contains a symbolic link")
            for name in files:
                file_path = current_path / name
                file_stat = file_path.lstat()
                if (
                    file_path.is_symlink()
                    or not stat.S_ISREG(file_stat.st_mode)
                    or file_stat.st_nlink != 1
                ):
                    raise DeploymentContractError("pinned runtime source worktree file is unsafe")
                executable = bool(file_stat.st_mode & 0o111)
                os.chmod(file_path, 0o555 if executable else 0o444)
            for name in directories:
                directory_path = current_path / name
                if directory_path.is_symlink():
                    raise DeploymentContractError("pinned runtime source worktree contains a symbolic link")
                os.chmod(directory_path, 0o555)
            os.chmod(current_path, 0o555)
    except OSError as exc:
        raise DeploymentContractError("pinned runtime source worktree cannot be sealed") from exc


def _reject_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            current_stat = current.lstat()
        except OSError as exc:
            raise DeploymentContractError(f"{label} cannot be inspected") from exc
        if stat.S_ISLNK(current_stat.st_mode):
            raise DeploymentContractError(f"{label} must not contain a symbolic link")


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DeploymentContractError("pinned runtime source state path cannot be inspected") from exc
    return True


def _revision_output(raw: str, *, label: str) -> str:
    value = raw.strip()
    if _REVISION.fullmatch(value) is None:
        raise DeploymentContractError(f"{label} is invalid")
    return value
