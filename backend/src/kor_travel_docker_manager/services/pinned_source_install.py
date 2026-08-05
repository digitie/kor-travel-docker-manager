"""F1E의 trusted pinned source selection transaction.

이 모듈은 의도적으로 ComposeService와 Docker SDK를 import하지 않는다. production
source cache의 user-owned Git worktree는 origin 문자열을 확인하는 source-owner helper
입력일 뿐이고, root Git process는 그 checkout의 config/remote/hook을 읽지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    c6c_state_paths,
    ensure_c6c_state_directory,
)
from kor_travel_docker_manager.services.cache_target_enable import (
    read_canonical_env_file,
    replace_canonical_env_file,
)
from kor_travel_docker_manager.services.cache_target_production_manifest import (
    CACHE_TARGET_PRODUCTION_PINS,
)

_MAX_ENV_BYTES = 1_048_576
_MAX_JOURNAL_BYTES = 65_536
_STATE_DIRECTORY_NAME = "pinned-sources-v1"
_JOURNAL_NAME = "pinned-source-install-v1.json"
_BACKUP_NAME = "pinned-source-install-v1.env.backup"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
_DOTENV_KEY_PATTERN = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
_TERMINAL_PHASES = frozenset({"committed", "rolled_back"})
_KNOWN_PHASES = frozenset(
    {
        "prepared",
        "env_replaced",
        "committed",
        "rollback_preparing",
        "rolled_back",
    }
)


@dataclass(frozen=True)
class RepoSpec:
    label: str
    source_key: str
    revision_key: str
    canonical_url: str
    revision: str


@dataclass(frozen=True)
class SourceSelection:
    source_roots: Mapping[str, Path]
    rendered_env: bytes


@dataclass(frozen=True)
class SourceIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int


@dataclass(frozen=True)
class PinnedSourceInstallPaths:
    state_directory: Path
    journal: Path
    backup: Path
    sources_directory: Path


GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def pinned_repo_specs() -> tuple[RepoSpec, RepoSpec]:
    """tracked release pin을 유일한 source authority로 변환한다."""

    pinvi_revision = CACHE_TARGET_PRODUCTION_PINS.pinvi_release_revision
    if pinvi_revision is None:
        raise DeploymentContractError("pinned source installer requires a PinVi release pin")
    specs = (
        RepoSpec(
            label="map",
            source_key="KOR_TRAVEL_MAP_REPO_DIR",
            revision_key="KOR_TRAVEL_MAP_GIT_COMMIT",
            canonical_url="https://github.com/digitie/kor-travel-map.git",
            revision=CACHE_TARGET_PRODUCTION_PINS.map_release_revision,
        ),
        RepoSpec(
            label="pinvi",
            source_key="PINVI_REPO_DIR",
            revision_key="PINVI_SOURCE_REVISION",
            canonical_url="https://github.com/digitie/pinvi.git",
            revision=pinvi_revision,
        ),
    )
    if any(_REVISION_PATTERN.fullmatch(spec.revision) is None for spec in specs):
        raise DeploymentContractError("tracked pinned source revision is invalid")
    return specs


def pinned_source_install_paths(values: Mapping[str, str]) -> PinnedSourceInstallPaths:
    manifest_path, _ = c6c_state_paths(values)
    state_directory = Path(manifest_path).parent
    sources_directory = state_directory / _STATE_DIRECTORY_NAME
    return PinnedSourceInstallPaths(
        state_directory=state_directory,
        journal=state_directory / _JOURNAL_NAME,
        backup=state_directory / _BACKUP_NAME,
        sources_directory=sources_directory,
    )


def parse_pinned_source_selection(
    raw: bytes,
    *,
    specs: Sequence[RepoSpec],
    target_roots: Mapping[str, Path],
) -> SourceSelection:
    """source-selection keyset만 strict dotenv로 읽고 동일 bytes를 보존해 교체한다."""

    if not raw or len(raw) > _MAX_ENV_BYTES:
        raise DeploymentContractError("canonical env size is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentContractError("canonical env must be UTF-8") from exc

    by_key = {spec.source_key: spec for spec in specs}
    revision_by_key = {spec.revision_key: spec for spec in specs}
    selected = frozenset((*by_key, *revision_by_key))
    occurrences: dict[str, list[tuple[int, str, str]]] = {key: [] for key in selected}
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        stripped = content.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        matched = _DOTENV_KEY_PATTERN.fullmatch(stripped)
        if matched is None:
            if any(key in stripped for key in selected):
                raise DeploymentContractError("pinned source dotenv declaration is invalid")
            continue
        key, value = matched.groups()
        if key not in selected:
            continue
        if stripped.startswith("export"):
            raise DeploymentContractError("pinned source dotenv declaration must not use export")
        if content != stripped:
            raise DeploymentContractError("pinned source dotenv declaration must be canonical")
        occurrences[key].append((index, value, line))

    source_roots: dict[str, Path] = {}
    replacement_lines = list(lines)
    missing_scalars: list[RepoSpec] = []
    for spec in specs:
        root_values = occurrences[spec.source_key]
        if len(root_values) != 1:
            raise DeploymentContractError("pinned source root must occur exactly once")
        _, root_value, _ = root_values[0]
        source_root = _strict_source_root(root_value, label=spec.label)
        source_roots[spec.label] = source_root
        target = target_roots.get(spec.label)
        if target is None:
            raise DeploymentContractError("pinned source target is missing")
        target_value = str(target)
        root_index = root_values[0][0]
        replacement_lines[root_index] = _replacement_line(
            spec.source_key,
            target_value,
            root_values[0][2],
        )

        scalar_values = occurrences[spec.revision_key]
        if len(scalar_values) > 1:
            raise DeploymentContractError("pinned source revision must occur at most once")
        if not scalar_values:
            missing_scalars.append(spec)
            continue
        scalar_index, scalar_value, scalar_line = scalar_values[0]
        if scalar_value != spec.revision:
            raise DeploymentContractError("pinned source revision scalar differs from release pin")
        replacement_lines[scalar_index] = _replacement_line(
            spec.revision_key,
            spec.revision,
            scalar_line,
        )

    rendered = "".join(replacement_lines)
    if missing_scalars:
        if rendered and not rendered.endswith(("\n", "\r")):
            rendered += "\n"
        rendered += "".join(
            f"{spec.revision_key}={spec.revision}\n" for spec in missing_scalars
        )
    encoded = rendered.encode("utf-8")
    if encoded == raw:
        raise DeploymentContractError("pinned source env replacement must change canonical bytes")
    return SourceSelection(source_roots=source_roots, rendered_env=encoded)


def _replacement_line(key: str, value: str, original_line: str) -> str:
    ending = "\r\n" if original_line.endswith("\r\n") else "\n" if original_line.endswith("\n") else ""
    return f"{key}={value}{ending}"


def _strict_source_root(value: str, *, label: str) -> Path:
    if not value or value != value.strip() or "$" in value or "`" in value:
        raise DeploymentContractError("pinned source root is blank or interpolated")
    path = Path(value)
    if (
        not path.is_absolute()
        or value != os.path.abspath(value)
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise DeploymentContractError(f"{label} pinned source root must be canonical absolute")
    _validate_no_symlink_path(path, label=f"{label} pinned source root")
    try:
        source_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{label} pinned source root cannot be inspected") from exc
    if not stat.S_ISDIR(source_stat.st_mode) or source_stat.st_uid == 0:
        raise DeploymentContractError(f"{label} pinned source root is not a user-owned directory")
    if source_stat.st_mode & 0o022:
        raise DeploymentContractError(f"{label} pinned source root is writable by a group or other")
    return path


def _validate_no_symlink_path(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            current_stat = current.lstat()
        except OSError as exc:
            raise DeploymentContractError(f"{label} cannot be inspected") from exc
        if stat.S_ISLNK(current_stat.st_mode):
            raise DeploymentContractError(f"{label} must not contain a symbolic link")


def canonical_source_identity(
    source_root: Path,
    *,
    spec: RepoSpec,
    runner: GitRunner = subprocess.run,
) -> SourceIdentity:
    """source owner identity로만 local origin을 읽는다.

    `git`는 child preexec에서 source UID/GID로 drop한 뒤 실행된다. root process는
    source-root의 Git config를 parse하지 않는다.
    """

    before = _source_identity(source_root, spec=spec)
    completed = runner(
        ["/usr/bin/git", "-C", str(source_root), "config", "--local", "--get", "remote.origin.url"],
        check=False,
        text=True,
        capture_output=True,
        cwd="/",
        env=_source_owner_git_environment(),
        preexec_fn=_drop_privileges(before.uid, before.gid),
    )
    if completed.returncode != 0:
        raise DeploymentContractError(f"{spec.label} source origin cannot be read as source owner")
    if completed.stdout != f"{spec.canonical_url}\n":
        raise DeploymentContractError(f"{spec.label} source origin is not the canonical HTTPS URL")
    after = _source_identity(source_root, spec=spec)
    if after != before:
        raise DeploymentContractError(f"{spec.label} source root changed during origin verification")
    return before


def _source_identity(source_root: Path, *, spec: RepoSpec) -> SourceIdentity:
    _validate_no_symlink_path(source_root, label=f"{spec.label} pinned source root")
    try:
        source_stat = source_root.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{spec.label} pinned source root cannot be inspected") from exc
    if (
        not stat.S_ISDIR(source_stat.st_mode)
        or source_stat.st_uid == 0
        or source_stat.st_mode & 0o022
    ):
        raise DeploymentContractError(f"{spec.label} pinned source root is unsafe")
    return SourceIdentity(
        device=source_stat.st_dev,
        inode=source_stat.st_ino,
        uid=source_stat.st_uid,
        gid=source_stat.st_gid,
        mode=stat.S_IMODE(source_stat.st_mode),
    )


def _drop_privileges(uid: int, gid: int) -> Callable[[], None]:
    def drop_privileges() -> None:
        os.setgroups([gid])
        os.setgid(gid)
        os.setuid(uid)

    return drop_privileges


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


def _run_root_git(
    arguments: Sequence[str],
    *,
    runner: GitRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
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
        raise DeploymentContractError("pinned source staging Git operation failed")
    return completed


def _ensure_root_directory(path: Path, *, mode: int) -> None:
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError("pinned source state directory is unavailable") from exc
    if (
        not stat.S_ISDIR(path_stat.st_mode)
        or path_stat.st_uid != os.geteuid()
        or stat.S_IMODE(path_stat.st_mode) != mode
    ):
        raise DeploymentContractError("pinned source state directory is unsafe")


def _write_private_backup(path: Path, raw: bytes) -> str:
    if not raw or len(raw) > _MAX_ENV_BYTES:
        raise DeploymentContractError("pinned source backup payload is invalid")
    _ensure_root_directory(path.parent, mode=0o700)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError as exc:
        raise DeploymentContractError("pinned source backup already exists") from exc
    except OSError as exc:
        raise DeploymentContractError("pinned source backup cannot be created") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise DeploymentContractError("pinned source backup cannot be written") from exc
    _fsync_directory(path.parent)
    return hashlib.sha256(raw).hexdigest()


def _read_private_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_uid != os.geteuid()
        or path_stat.st_nlink != 1
        or stat.S_IMODE(path_stat.st_mode) != 0o600
    ):
        raise DeploymentContractError(f"{label} is unsafe")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                opened.st_dev != path_stat.st_dev
                or opened.st_ino != path_stat.st_ino
                or opened.st_uid != path_stat.st_uid
                or opened.st_gid != path_stat.st_gid
                or stat.S_IMODE(opened.st_mode) != stat.S_IMODE(path_stat.st_mode)
                or opened.st_nlink != path_stat.st_nlink
            ):
                raise DeploymentContractError(f"{label} changed during open")
            payload = stream.read(maximum_bytes + 1)
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be read") from exc
    if not payload or len(payload) > maximum_bytes:
        raise DeploymentContractError(f"{label} payload is invalid")
    return payload


def _write_journal(path: Path, document: Mapping[str, Any]) -> None:
    payload = _serialize_journal(document)
    _ensure_root_directory(path.parent, mode=0o700)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _read_private_file(path, label="pinned source journal", maximum_bytes=_MAX_JOURNAL_BYTES)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DeploymentContractError("pinned source journal cannot be written") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_journal(path: Path) -> dict[str, Any]:
    raw = _read_private_file(path, label="pinned source journal", maximum_bytes=_MAX_JOURNAL_BYTES)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentContractError("pinned source journal is invalid") from exc
    if not isinstance(document, dict):
        raise DeploymentContractError("pinned source journal is invalid")
    _validate_journal(document)
    return document


def _serialize_journal(document: Mapping[str, Any]) -> bytes:
    copied = dict(document)
    _validate_journal(copied)
    return json.dumps(copied, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _validate_journal(document: Mapping[str, Any]) -> None:
    expected = {
        "version",
        "phase",
        "old_env_sha256",
        "new_env_sha256",
        "backup_sha256",
        "env_uid",
        "env_gid",
        "repositories",
    }
    if set(document) != expected or document.get("version") != 1:
        raise DeploymentContractError("pinned source journal schema is invalid")
    phase = document.get("phase")
    if phase not in _KNOWN_PHASES:
        raise DeploymentContractError("pinned source journal phase is invalid")
    for key in ("old_env_sha256", "new_env_sha256", "backup_sha256"):
        if not isinstance(document.get(key), str) or _SHA256_PATTERN.fullmatch(document[key]) is None:
            raise DeploymentContractError("pinned source journal digest is invalid")
    if document["old_env_sha256"] == document["new_env_sha256"]:
        raise DeploymentContractError("pinned source journal env transition is invalid")
    if type(document.get("env_uid")) is not int or type(document.get("env_gid")) is not int:
        raise DeploymentContractError("pinned source journal owner is invalid")
    repositories = document.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != 2:
        raise DeploymentContractError("pinned source journal repository evidence is invalid")
    specifications = {spec.label: spec for spec in pinned_repo_specs()}
    labels: set[str] = set()
    for repository in repositories:
        if not isinstance(repository, dict) or set(repository) != {
            "label",
            "source_root",
            "target_root",
            "revision",
            "tree",
            "source_identity",
        }:
            raise DeploymentContractError("pinned source journal repository evidence is invalid")
        label = repository["label"]
        if label not in specifications or label in labels:
            raise DeploymentContractError("pinned source journal repository label is invalid")
        labels.add(label)
        if (
            not isinstance(repository["source_root"], str)
            or not isinstance(repository["target_root"], str)
            or repository["revision"] != specifications[label].revision
            or _REVISION_PATTERN.fullmatch(repository["tree"]) is None
        ):
            raise DeploymentContractError("pinned source journal repository value is invalid")
        identity = repository["source_identity"]
        if not isinstance(identity, dict) or set(identity) != {"device", "inode", "uid", "gid", "mode"}:
            raise DeploymentContractError("pinned source journal source identity is invalid")
        if any(type(value) is not int for value in identity.values()):
            raise DeploymentContractError("pinned source journal source identity is invalid")
    if labels != set(specifications):
        raise DeploymentContractError("pinned source journal repository labels are incomplete")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DeploymentContractError("pinned source state directory cannot be synced") from exc


def _path_exists_lstat(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DeploymentContractError("pinned source state path cannot be inspected") from exc
    return True


def _journal_repositories(
    *,
    specs: Sequence[RepoSpec],
    source_roots: Mapping[str, Path],
    target_roots: Mapping[str, Path],
    identities: Mapping[str, SourceIdentity],
    trees: Mapping[str, str],
) -> list[dict[str, Any]]:
    return [
        {
            "label": spec.label,
            "source_root": str(source_roots[spec.label]),
            "target_root": str(target_roots[spec.label]),
            "revision": spec.revision,
            "tree": trees[spec.label],
            "source_identity": {
                "device": identities[spec.label].device,
                "inode": identities[spec.label].inode,
                "uid": identities[spec.label].uid,
                "gid": identities[spec.label].gid,
                "mode": identities[spec.label].mode,
            },
        }
        for spec in specs
    ]


def _initial_journal(
    *,
    raw: bytes,
    rendered: bytes,
    env_uid: int,
    env_gid: int,
    specs: Sequence[RepoSpec],
    source_roots: Mapping[str, Path],
    target_roots: Mapping[str, Path],
    identities: Mapping[str, SourceIdentity],
) -> dict[str, Any]:
    return {
        "version": 1,
        "phase": "prepared",
        "old_env_sha256": hashlib.sha256(raw).hexdigest(),
        "new_env_sha256": hashlib.sha256(rendered).hexdigest(),
        "backup_sha256": hashlib.sha256(raw).hexdigest(),
        "env_uid": env_uid,
        "env_gid": env_gid,
        "repositories": _journal_repositories(
            specs=specs,
            source_roots=source_roots,
            target_roots=target_roots,
            identities=identities,
            trees={spec.label: "0" * 40 for spec in specs},
        ),
    }


def _materialize_pinned_worktree(
    *,
    paths: PinnedSourceInstallPaths,
    spec: RepoSpec,
    runner: GitRunner,
) -> str:
    bare_directory = paths.sources_directory / ".bare"
    target = paths.sources_directory / spec.label / spec.revision
    _ensure_root_directory(paths.sources_directory, mode=0o700)
    _ensure_root_directory(bare_directory, mode=0o700)
    _ensure_root_directory(target.parent, mode=0o700)
    bare = bare_directory / f"{spec.label}.git"
    if _path_exists_lstat(bare) and not bare.is_dir():
        raise DeploymentContractError("pinned source bare staging path is unsafe")
    if not _path_exists_lstat(bare):
        _run_root_git(["init", "--bare", str(bare)], runner=runner)
        os.chmod(bare, 0o700)
    _validate_root_owned_tree(bare, mode=0o700, label="pinned source bare staging")

    if _path_exists_lstat(target):
        _assert_no_pinned_submodules(bare=bare, spec=spec, runner=runner)
        return _validate_existing_pinned_worktree(target, spec=spec, runner=runner)
    _run_root_git(
        [
            "--git-dir",
            str(bare),
            "fetch",
            "--no-tags",
            spec.canonical_url,
            spec.revision,
        ],
        runner=runner,
    )
    _run_root_git(
        ["--git-dir", str(bare), "cat-file", "-e", f"{spec.revision}^{{commit}}"],
        runner=runner,
    )
    _assert_no_pinned_submodules(bare=bare, spec=spec, runner=runner)
    tree = _run_root_git(
        ["--git-dir", str(bare), "rev-parse", f"{spec.revision}^{{tree}}"],
        runner=runner,
    ).stdout.strip()
    if _REVISION_PATTERN.fullmatch(tree) is None:
        raise DeploymentContractError("pinned source tree identity is invalid")
    _run_root_git(
        ["--git-dir", str(bare), "worktree", "add", "--detach", str(target), spec.revision],
        runner=runner,
    )
    status = _run_root_git(
        ["-C", str(target), "status", "--porcelain=v1", "--untracked-files=normal"],
        runner=runner,
    ).stdout
    if status:
        raise DeploymentContractError("pinned source worktree is not clean")
    _make_worktree_immutable(target)
    return tree


def _assert_no_pinned_submodules(
    *,
    bare: Path,
    spec: RepoSpec,
    runner: GitRunner,
) -> None:
    tree_entries = _run_root_git(
        ["--git-dir", str(bare), "ls-tree", "-r", "--full-tree", spec.revision],
        runner=runner,
    ).stdout.splitlines()
    if any(entry.startswith("160000 commit ") for entry in tree_entries):
        raise DeploymentContractError("pinned source repository must not contain submodules")


def _validate_existing_pinned_worktree(
    target: Path,
    *,
    spec: RepoSpec,
    runner: GitRunner,
) -> str:
    _validate_root_owned_tree(target, mode=0o555, label="pinned source worktree")
    revision = _run_root_git(["-C", str(target), "rev-parse", "--verify", "HEAD"], runner=runner).stdout.strip()
    if revision != spec.revision:
        raise DeploymentContractError("pinned source worktree revision drifted")
    tree = _run_root_git(["-C", str(target), "rev-parse", "HEAD^{tree}"], runner=runner).stdout.strip()
    if _REVISION_PATTERN.fullmatch(tree) is None:
        raise DeploymentContractError("pinned source worktree tree is invalid")
    status = _run_root_git(
        ["-C", str(target), "status", "--porcelain=v1", "--untracked-files=normal"],
        runner=runner,
    ).stdout
    if status:
        raise DeploymentContractError("pinned source worktree is not clean")
    return tree


def _validate_root_owned_tree(path: Path, *, mode: int, label: str) -> None:
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise DeploymentContractError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISDIR(path_stat.st_mode)
        or path_stat.st_uid != os.geteuid()
        or stat.S_IMODE(path_stat.st_mode) != mode
    ):
        raise DeploymentContractError(f"{label} is unsafe")


def _make_worktree_immutable(target: Path) -> None:
    for current, directories, files in os.walk(target, topdown=False, followlinks=False):
        current_path = Path(current)
        if current_path.is_symlink():
            raise DeploymentContractError("pinned source worktree contains a symbolic link")
        for name in files:
            file_path = current_path / name
            file_stat = file_path.lstat()
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise DeploymentContractError("pinned source worktree file is unsafe")
            executable = bool(file_stat.st_mode & 0o111)
            os.chmod(file_path, 0o555 if executable else 0o444)
        for name in directories:
            directory_path = current_path / name
            if directory_path.is_symlink():
                raise DeploymentContractError("pinned source worktree contains a symbolic link")
            os.chmod(directory_path, 0o555)
        os.chmod(current_path, 0o555)


def _transition_journal(document: Mapping[str, Any], phase: str) -> dict[str, Any]:
    current = document.get("phase")
    if not isinstance(current, str):
        raise DeploymentContractError("pinned source journal phase is invalid")
    allowed = {
        "prepared": {"env_replaced", "rollback_preparing"},
        "env_replaced": {"committed", "rollback_preparing"},
        "rollback_preparing": {"rolled_back"},
        "rolled_back": {"prepared"},
        "committed": set(),
    }
    if phase not in allowed.get(current, set()):
        raise DeploymentContractError("pinned source journal transition is invalid")
    transitioned = {**document, "phase": phase}
    _validate_journal(transitioned)
    return transitioned


def _validate_journal_env_and_backup(
    *,
    paths: PinnedSourceInstallPaths,
    journal: Mapping[str, Any],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> bytes:
    backup = _read_private_file(paths.backup, label="pinned source backup", maximum_bytes=_MAX_ENV_BYTES)
    if hashlib.sha256(backup).hexdigest() != journal["backup_sha256"]:
        raise DeploymentContractError("pinned source backup digest drifted")
    if journal["env_uid"] != expected_owner_uid or journal["env_gid"] != expected_owner_gid:
        raise DeploymentContractError("pinned source journal env owner differs from frozen env")
    current = read_canonical_env_file(
        env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    current_sha256 = hashlib.sha256(current).hexdigest()
    if current_sha256 not in {journal["old_env_sha256"], journal["new_env_sha256"]}:
        raise DeploymentContractError("canonical env differs from pinned source transaction")
    return current


def _verify_committed_journal(
    *,
    paths: PinnedSourceInstallPaths,
    journal: Mapping[str, Any],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> None:
    current = _validate_journal_env_and_backup(
        paths=paths,
        journal=journal,
        env_path=env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    if hashlib.sha256(current).hexdigest() != journal["new_env_sha256"]:
        raise DeploymentContractError("committed pinned source transaction env drifted")
    specifications = {spec.label: spec for spec in pinned_repo_specs()}
    for entry in journal["repositories"]:
        spec = specifications.get(entry["label"])
        if spec is None or entry["revision"] != spec.revision:
            raise DeploymentContractError("committed pinned source release evidence drifted")
        target = Path(entry["target_root"])
        if target != paths.sources_directory / spec.label / spec.revision:
            raise DeploymentContractError("committed pinned source target escaped state directory")
        tree = _validate_existing_pinned_worktree(
            target,
            spec=spec,
            runner=subprocess.run,
        )
        if tree != entry["tree"] or tree == "0" * 40:
            raise DeploymentContractError("committed pinned source tree evidence drifted")


def _rollback_env_if_possible(
    *,
    paths: PinnedSourceInstallPaths,
    journal: Mapping[str, Any],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> None:
    current = _validate_journal_env_and_backup(
        paths=paths,
        journal=journal,
        env_path=env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    current_sha256 = hashlib.sha256(current).hexdigest()
    if current_sha256 == journal["new_env_sha256"]:
        backup = _read_private_file(paths.backup, label="pinned source backup", maximum_bytes=_MAX_ENV_BYTES)
        replace_canonical_env_file(
            env_path,
            expected_sha256=journal["new_env_sha256"],
            replacement=backup,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )


def install_pinned_sources(
    *,
    environment: Mapping[str, str],
    env_path: Path,
    env_bytes: bytes,
    expected_owner_uid: int,
    expected_owner_gid: int,
    runner: GitRunner = subprocess.run,
) -> dict[str, Any]:
    """root-only source authority transaction을 실행하거나 exact crash state를 resume한다."""

    if os.geteuid() != 0:
        raise DeploymentContractError("pinned source installation requires root")
    if environment.get("KTDM_DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "production":
        raise DeploymentContractError("pinned source installation is available only in production")
    specs = pinned_repo_specs()
    paths = pinned_source_install_paths(environment)
    ensure_c6c_state_directory(paths.state_directory)
    _ensure_root_directory(paths.state_directory, mode=0o700)
    target_roots = {
        spec.label: paths.sources_directory / spec.label / spec.revision for spec in specs
    }

    journal = _read_journal(paths.journal) if _path_exists_lstat(paths.journal) else None
    if journal is not None:
        if journal["phase"] == "committed":
            _verify_committed_journal(
                paths=paths,
                journal=journal,
                env_path=env_path,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
            )
            return {"success": True, "state": "committed", "resumed": True}
        if journal["phase"] == "rollback_preparing":
            _rollback_env_if_possible(
                paths=paths,
                journal=journal,
                env_path=env_path,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
            )
            journal = _transition_journal(journal, "rolled_back")
            _write_journal(paths.journal, journal)
        if journal["phase"] == "rolled_back":
            journal = _transition_journal(journal, "prepared")
            _write_journal(paths.journal, journal)
        current = _validate_journal_env_and_backup(
            paths=paths,
            journal=journal,
            env_path=env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
        if hashlib.sha256(current).hexdigest() == journal["new_env_sha256"]:
            if journal["phase"] == "prepared":
                journal = _transition_journal(journal, "env_replaced")
                _write_journal(paths.journal, journal)
            _verify_committed_journal(
                paths=paths,
                journal=journal,
                env_path=env_path,
                expected_owner_uid=expected_owner_uid,
                expected_owner_gid=expected_owner_gid,
            )
            journal = _transition_journal(journal, "committed")
            _write_journal(paths.journal, journal)
            return {"success": True, "state": "committed", "resumed": True}
    else:
        if _path_exists_lstat(paths.backup) or _path_exists_lstat(paths.sources_directory):
            raise DeploymentContractError("foreign pinned source installation residue blocks mutation")
        raw = read_canonical_env_file(
            env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
        if raw != env_bytes:
            raise DeploymentContractError("canonical env changed after frozen snapshot")
        selection = parse_pinned_source_selection(raw, specs=specs, target_roots=target_roots)
        identities = {
            spec.label: canonical_source_identity(
                selection.source_roots[spec.label], spec=spec, runner=runner
            )
            for spec in specs
        }
        _write_private_backup(paths.backup, raw)
        journal = _initial_journal(
            raw=raw,
            rendered=selection.rendered_env,
            env_uid=expected_owner_uid,
            env_gid=expected_owner_gid,
            specs=specs,
            source_roots=selection.source_roots,
            target_roots=target_roots,
            identities=identities,
        )
        _write_journal(paths.journal, journal)

    try:
        source_roots = {entry["label"]: Path(entry["source_root"]) for entry in journal["repositories"]}
        identities = {
            entry["label"]: SourceIdentity(**entry["source_identity"])
            for entry in journal["repositories"]
        }
        for spec in specs:
            if canonical_source_identity(source_roots[spec.label], spec=spec, runner=runner) != identities[spec.label]:
                raise DeploymentContractError("pinned source owner input drifted before materialization")
        trees = {
            spec.label: _materialize_pinned_worktree(paths=paths, spec=spec, runner=runner)
            for spec in specs
        }
        journal = {
            **journal,
            "repositories": _journal_repositories(
                specs=specs,
                source_roots=source_roots,
                target_roots=target_roots,
                identities=identities,
                trees=trees,
            ),
        }
        _write_journal(paths.journal, journal)
        backup = _read_private_file(paths.backup, label="pinned source backup", maximum_bytes=_MAX_ENV_BYTES)
        rendered_selection = parse_pinned_source_selection(
            backup,
            specs=specs,
            target_roots=target_roots,
        )
        if hashlib.sha256(rendered_selection.rendered_env).hexdigest() != journal["new_env_sha256"]:
            raise DeploymentContractError("pinned source rendered env differs from durable journal")
        replace_canonical_env_file(
            env_path,
            expected_sha256=journal["old_env_sha256"],
            replacement=rendered_selection.rendered_env,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
        journal = _transition_journal(journal, "env_replaced")
        _write_journal(paths.journal, journal)
        _verify_committed_journal(
            paths=paths,
            journal=journal,
            env_path=env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
        journal = _transition_journal(journal, "committed")
        _write_journal(paths.journal, journal)
    except Exception:
        try:
            if journal["phase"] not in _TERMINAL_PHASES:
                journal = _transition_journal(journal, "rollback_preparing")
                _write_journal(paths.journal, journal)
                _rollback_env_if_possible(
                    paths=paths,
                    journal=journal,
                    env_path=env_path,
                    expected_owner_uid=expected_owner_uid,
                    expected_owner_gid=expected_owner_gid,
                )
                journal = _transition_journal(journal, "rolled_back")
                _write_journal(paths.journal, journal)
        except Exception as rollback_error:
            raise DeploymentContractError("pinned source installation failed and rollback is incomplete") from rollback_error
        raise
    return {"success": True, "state": "committed", "resumed": False}


def assert_pinned_source_installation_allows_pair_mutation(
    *,
    environment: Mapping[str, str],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> None:
    """unfinished/foreign installer residue가 pair mutation을 덮지 못하게 막는다."""

    paths = pinned_source_install_paths(environment)
    journal_exists = _path_exists_lstat(paths.journal)
    residue_exists = _path_exists_lstat(paths.backup) or _path_exists_lstat(
        paths.sources_directory
    )
    if not journal_exists:
        if residue_exists:
            raise DeploymentContractError("foreign pinned source installation residue blocks pair mutation")
        return
    journal = _read_journal(paths.journal)
    if journal["phase"] not in _TERMINAL_PHASES:
        raise DeploymentContractError("unfinished pinned source installation blocks pair mutation")
    current = _validate_journal_env_and_backup(
        paths=paths,
        journal=journal,
        env_path=env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    if (
        journal["phase"] == "rolled_back"
        and hashlib.sha256(current).hexdigest() != journal["old_env_sha256"]
    ):
        raise DeploymentContractError(
            "rolled-back pinned source transaction requires the original canonical env"
        )
    if journal["phase"] == "committed":
        _verify_committed_journal(
            paths=paths,
            journal=journal,
            env_path=env_path,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )


def require_committed_pinned_source_installation(
    *,
    environment: Mapping[str, str],
    env_path: Path,
    expected_owner_uid: int,
    expected_owner_gid: int,
) -> None:
    """F1D가 신뢰된 exact source selection 뒤에만 실행되도록 강제한다.

    일반 pair mutation은 F1E가 도입되기 전의 정상 상태도 허용할 수 있지만, F1D는
    root가 build provenance를 읽기 전에 F1E의 committed evidence와 root-owned
    exact worktree를 반드시 확인해야 한다.
    """

    paths = pinned_source_install_paths(environment)
    if not _path_exists_lstat(paths.journal):
        raise DeploymentContractError(
            "pinned drift bootstrap requires a committed pinned source installation"
        )
    journal = _read_journal(paths.journal)
    if journal["phase"] != "committed":
        raise DeploymentContractError(
            "pinned drift bootstrap requires a committed pinned source installation"
        )
    _verify_committed_journal(
        paths=paths,
        journal=journal,
        env_path=env_path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
