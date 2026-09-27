"""pinned runtime 재구축이 빌드하는 source 트리 (ADR-51 E).

source는 revision으로 이름 붙은 디렉터리다 — ``<state_root>/pinned-runtime-sources/<role>-<revision>/``
안에 ``tree/``(그 revision의 ``git archive``)와 ``source.json``(role·revision·tree)이 있다. 없을 때만
canonical HTTPS에서 그 revision을 얕게 받아 만들고, 있으면 git을 부르지 않는다 — SHA가 곧 증명이고
디렉터리 이름이 그 SHA다. 운영자 checkout(``.env``의 ``*_REPO_DIR``)은 입력이 아니다.

M05 본문은 이 트리가 아니라 실행별 checkout(``checkout_pinned_run_source``)에서 돈다 — PinVi
attestation이 진짜 git checkout을 요구하기 때문이다(ADR-51 E-2).

root git은 사람의 설정을 읽지 않는다. hook·credential helper·file/ext 프로토콜을 끄고, HOME과
global/system config를 비우고, 프로토콜을 HTTPS로 제한한다(``_run_root_git``) — 비-root 사용자의
gitconfig가 ``insteadOf``로 URL을 바꿔 root로 코드를 돌리지 못하게 한다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from kor_travel_docker_manager.services.c6c_deployment import DeploymentContractError
from kor_travel_docker_manager.services.errors import command_output_tail
from kor_travel_docker_manager.services.pinned_runtime_generation import (
    PinnedRuntimeStatePaths,
    ensure_pinned_runtime_state_directory,
)
from kor_travel_docker_manager.services.pinned_runtime_release import (
    RUNTIME_SOURCE_ROLES,
    PinnedRuntimeRelease,
    PinnedRuntimeSourceSpec,
    RuntimeSourceRole,
)

_SOURCES_DIRECTORY_NAME = "pinned-runtime-sources"
_SOURCE_RECORD_NAME = "source.json"
_TREE_DIRECTORY_NAME = "tree"
_REVISION = re.compile(r"^[0-9a-f]{40}$")

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class MaterializedRuntimeSource:
    """후보 build가 소비할 source root와 그 Git 증거."""

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
    """candidate build와 배포 기록(``deploy-status.json``)이 공유하는 source pinset 결과."""

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


def pinned_runtime_sources_directory(state_paths: PinnedRuntimeStatePaths) -> Path:
    return state_paths.state_root / _SOURCES_DIRECTORY_NAME


def materialize_pinned_runtime_sources(
    *,
    release: PinnedRuntimeRelease,
    state_paths: PinnedRuntimeStatePaths,
    runner: GitRunner = subprocess.run,
) -> PinnedRuntimeSourceMaterialization:
    """release의 source 트리를 보장한다. 이미 있는 revision은 git 없이 그대로 쓴다.

    state root는 먼저 이 프로세스 소유의 ``0700``인지 검증한다 — M05 preflight는 이 호출 뒤에
    같은 root의 ``deploy-status.json``을 읽으므로 순서가 중요하다.
    """

    ensure_pinned_runtime_state_directory(state_paths.state_root)
    sources_directory = pinned_runtime_sources_directory(state_paths)
    try:
        sources_directory.mkdir(mode=0o700, exist_ok=True)
        metadata = sources_directory.lstat()
    except OSError as exc:
        raise DeploymentContractError("pinned runtime source directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise DeploymentContractError("pinned runtime source directory is unsafe")
    materialized = tuple(
        _materialize_source(sources_directory, source=source, runner=runner)
        for source in release.sources
    )
    return PinnedRuntimeSourceMaterialization(release=release, sources=materialized)


def prune_pinned_runtime_sources(
    state_paths: PinnedRuntimeStatePaths,
    *,
    keep: PinnedRuntimeSourceMaterialization,
) -> None:
    """``keep``의 두 source 말고는 옛 revision과 끊긴 시도를 지운다.

    재구축이 G 안에서 materialize 직후 부른다. 지우지 못한 것은 다음 재구축이 다시 지운다 —
    배포를 실패시키지 않는다.
    """

    sources_directory = pinned_runtime_sources_directory(state_paths)
    kept = {_source_directory_name(source.role, source.revision) for source in keep.sources}
    try:
        entries = list(sources_directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name in kept:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                # 기록부터 지운다 — 삭제가 끊겨도 반쪽 트리는 기록 없는 디렉터리라 다시 만들어진다.
                (entry / _SOURCE_RECORD_NAME).unlink(missing_ok=True)
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            continue


def _source_directory_name(role: str, revision: str) -> str:
    return f"{role}-{revision}"


def _materialize_source(
    sources_directory: Path,
    *,
    source: PinnedRuntimeSourceSpec,
    runner: GitRunner,
) -> MaterializedRuntimeSource:
    final = sources_directory / _source_directory_name(source.role, source.revision)
    existing = _read_source(final, source=source)
    if existing is not None:
        return existing
    partial = sources_directory / (
        f".{_source_directory_name(source.role, source.revision)}.partial-{uuid.uuid4().hex}"
    )
    partial.mkdir(mode=0o700)
    try:
        tree = _fetch_and_extract(partial, source=source, runner=runner)
        record = {"role": source.role, "revision": source.revision, "tree": tree}
        (partial / _SOURCE_RECORD_NAME).write_text(
            json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
        )
        # 이름을 붙이기 전에 내용을 디스크에 둔다 — 이름이 붙은 트리는 git 없이 재사용되므로,
        # 전원이 끊겨 빈 파일로 남은 트리가 다음 빌드의 context가 되면 안 된다.
        os.sync()
        try:
            os.rename(partial, final)
        except OSError:
            # 같은 revision을 동시에 만든 다른 실행이 먼저 놓았다 — 그 결과를 쓴다.
            shutil.rmtree(partial, ignore_errors=True)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    materialized = _read_source(final, source=source)
    if materialized is None:
        raise DeploymentContractError("pinned runtime source could not be placed")
    return materialized


def _fetch_and_extract(
    partial: Path,
    *,
    source: PinnedRuntimeSourceSpec,
    runner: GitRunner,
) -> str:
    """그 revision만 얕게 받아 ``partial/tree``에 archive를 풀고 tree id를 돌려준다."""

    bare = partial / "repo.git"
    _run_root_git(["init", "-q", "--bare", str(bare)], runner=runner)
    # archive가 곧 tree가 되게 한다 — 저장소의 export-ignore·export-subst를 끈다.
    (bare / "info").mkdir(exist_ok=True)
    (bare / "info" / "attributes").write_text(
        "* -export-ignore -export-subst\n", encoding="utf-8"
    )
    git_dir = ["--git-dir", str(bare)]
    _run_root_git(
        [
            *git_dir,
            "fetch",
            "-q",
            "--depth",
            "1",
            "--no-tags",
            source.canonical_url,
            source.revision,
        ],
        runner=runner,
    )
    _run_root_git([*git_dir, "cat-file", "-e", f"{source.revision}^{{commit}}"], runner=runner)
    tree = _revision_output(
        _run_root_git(
            [*git_dir, "rev-parse", "--verify", f"{source.revision}^{{tree}}"],
            runner=runner,
        ).stdout,
        label="pinned runtime source tree",
    )
    archive = partial / "source.tar"
    _run_root_git(
        [
            "-c",
            "tar.umask=0022",
            *git_dir,
            "archive",
            "--format=tar",
            "-o",
            str(archive),
            source.revision,
        ],
        runner=runner,
    )
    tree_directory = partial / _TREE_DIRECTORY_NAME
    tree_directory.mkdir()
    try:
        with tarfile.open(archive) as handle:
            # data 필터: 트리 밖을 가리키는 경로·링크와 특수 파일을 거부하고, group/other 쓰기와
            # setuid류 비트를 지운다. 파일의 실행 비트는 git index 그대로다.
            handle.extractall(tree_directory, filter="data")
        # data 필터는 디렉터리 mode를 적용하지 않아 호출자의 umask(M05는 077)를 따른다. 이 트리는
        # 이미지 build context이므로 디렉터리를 고정 0755로 둔다 — 누가 먼저 만들었는지에 따라
        # 이미지 안의 디렉터리 권한이 달라지면 안 된다.
        os.chmod(tree_directory, 0o755)
        for current, directories, _files in os.walk(tree_directory):
            for name in directories:
                os.chmod(os.path.join(current, name), 0o755)
    except (OSError, tarfile.TarError) as exc:
        raise DeploymentContractError("pinned runtime source archive cannot be extracted") from exc
    archive.unlink()
    shutil.rmtree(bare)
    return tree


def _read_source(final: Path, *, source: PinnedRuntimeSourceSpec) -> MaterializedRuntimeSource | None:
    """이름 붙은 source를 읽는다. 없으면 ``None``.

    기록이 없거나 읽히지 않거나 다른 revision을 가리키면 그 디렉터리를 지우고 ``None``을 돌려준다
    — SHA로 이름 붙은 캐시일 뿐이라 다시 만드는 것이 언제나 안전하다(끊긴 GC, 손상된 기록).
    """

    record_path = final / _SOURCE_RECORD_NAME
    tree_root = final / _TREE_DIRECTORY_NAME
    record: object
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not final.exists():
            return None
        record = None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        record = None
    if (
        not isinstance(record, dict)
        or record.get("role") != source.role
        or record.get("revision") != source.revision
        or not isinstance(record.get("tree"), str)
        or _REVISION.fullmatch(record["tree"]) is None
        or not tree_root.is_dir()
    ):
        try:
            shutil.rmtree(final)
        except OSError as exc:
            raise DeploymentContractError("pinned runtime source cannot be replaced") from exc
        return None
    return MaterializedRuntimeSource(
        role=source.role,
        root=tree_root,
        revision=source.revision,
        tree=record["tree"],
    )


def checkout_pinned_run_source(
    *,
    release: PinnedRuntimeRelease,
    role: RuntimeSourceRole,
    destination: Path,
    extra_revisions: Sequence[str] = (),
    runner: GitRunner = subprocess.run,
) -> Path:
    """M05 실행 하나가 쓰는 핀 소스 checkout을 새로 만든다(ADR-51 E-2).

    canonical HTTPS에서 그 revision만 얕게 받아 detached로 checkout한다 — SHA가 곧 증명이다.
    archive가 아니라 checkout인 이유는 PinVi attestation이 진짜 clean checkout
    (`--show-toplevel`, HEAD, `status`)과 service 릴리스 revision의 blob을 요구하기 때문이다.
    ``extra_revisions``는 그 blob을 읽을 commit이다. ``destination``은 아직 없어야 하고, 실행의
    output leaf 안에 둔다.
    """

    source = _release_source(release, role)
    for revision in extra_revisions:
        if _REVISION.fullmatch(revision) is None:
            raise DeploymentContractError("pinned run source extra revision is invalid")
    destination.mkdir(mode=0o700)
    _run_root_git(["init", "-q", str(destination)], runner=runner)
    _run_root_git(
        [
            "-C",
            str(destination),
            "fetch",
            "-q",
            "--depth",
            "1",
            "--no-tags",
            source.canonical_url,
            source.revision,
            *extra_revisions,
        ],
        runner=runner,
    )
    _run_root_git(
        ["-C", str(destination), "checkout", "-q", "--detach", source.revision],
        runner=runner,
    )
    return destination


def _release_source(
    release: PinnedRuntimeRelease, role: RuntimeSourceRole
) -> PinnedRuntimeSourceSpec:
    for source in release.sources:
        if source.role == role:
            return source
    raise DeploymentContractError("pinned runtime release has no such source role")


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
        # 원인 원문을 싣는다(ADR-51 잃는 보장 G). 첫 줄은 상수로 둔다 — M05 preflight가
        # `pinned runtime source ` 문구의 첫 줄만 stdout에 낸다. 명령(경로 포함)은 그 아래다.
        raise DeploymentContractError(
            f"pinned runtime source Git operation failed (exit {completed.returncode})"
            f"\n--- command ---\ngit {' '.join(arguments)}"
            + command_output_tail("stderr", completed.stderr)
        )
    return completed


def _root_git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        # 조회가 index를 갱신(=쓰기)하지 않게 한다. `source_status.py`와 같은 계약이다.
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
    }


def _revision_output(raw: str, *, label: str) -> str:
    value = raw.strip()
    if _REVISION.fullmatch(value) is None:
        raise DeploymentContractError(f"{label} is invalid")
    return value
