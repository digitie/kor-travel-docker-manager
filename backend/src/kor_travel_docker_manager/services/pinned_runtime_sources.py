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

    _require_canonical_rebuildable_state_paths(
        state_paths=state_paths,
        values=values,
        release=release,
    )
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
    release: PinnedRuntimeRelease,
) -> None:
    """호출자가 v5 rebuild state 밖으로 source를 유도하지 못하게 막는다."""

    expected = canonical_pinned_runtime_state_paths(
        values,
        pinset_sha256=release.pinset_sha256,
    )
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


def materialize_disposable_run_worktree(
    *,
    release: PinnedRuntimeRelease,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
    role: RuntimeSourceRole,
    expected_tree: str,
    destination: Path,
    runner: GitRunner = subprocess.run,
) -> Path:
    """**쓰기가 허용된** 일회용 실행 체크아웃을 만든다.

    핀 source worktree는 불변으로 봉인된다(0555/0444). 그런데 격리 e2e는 그 트리를
    컨테이너에 root RW로 마운트하고 그 안에서 `npm ci`와 Playwright를 돌린다 — root는
    모드를 무시하므로 `apps/web/node_modules`가 실제로 쓰이고, Docker가 만드는
    마운트포인트 3개가 0755 디렉터리로 남는다. 그러면 다음 preflight의
    `_validate_immutable_tree`가 정당하게 거부해 **같은 pinset 재실행이 불가능해진다**
    (2026-09-03·04 연속 재현).

    그래서 실행은 봉인된 트리가 아니라 여기서 만드는 일회용 체크아웃에서 한다. 이
    체크아웃은 **object store에서 재유도**된다 — 같은 bare 저장소, 같은 revision,
    같은 tree object다. 디스크 사본이 아니므로 파일 모드나 잔여물을 물려받지 않고,
    provenance는 아래 세 검사가 지탱한다: HEAD가 핀 revision일 것, tree object가
    일치할 것, 체크아웃이 clean일 것.

    불변 봉인은 **걸지 않는다.** 쓰기가 목적이기 때문이다. 대신 호출자가 실행 뒤
    `summarize_disposable_run_worktree`로 무엇이 남았는지 증거를 남기고,
    `remove_disposable_run_worktree`로 지운 뒤 봉인된 원본의 모드가 그대로인지
    `assert_pinned_worktree_is_still_sealed`로 본다. 그 사후조건이 관측하는 것은
    **엔트리 추가·삭제·모드 변경이 없었다**까지이며, root의 in-place 내용 변조는
    잡지 못한다(2026-09-04 journal §1).

    destination은 **실행마다 유일해야 한다.** 비정상 종료(SIGTERM/SIGHUP)로 cleanup이
    건너뛰어지면 bare에 admin 엔트리가 남는데, 같은 경로를 재사용하면 다음
    `worktree add`가 "missing but already registered"로 죽는다(적대 리뷰 #1 실측).

    `npm ci`가 만드는 `node_modules`가 여기에 쌓이므로 destination 파티션은 수백 MB의
    여유를 필요로 한다.
    """

    _require_canonical_rebuildable_state_paths(
        state_paths=state_paths,
        values=values,
        release=release,
    )
    paths = pinned_runtime_source_paths(state_paths=state_paths, release=release)
    source = _release_source(release, role)
    bare = paths.bare_repository(role)
    if not _path_exists(bare):
        raise DeploymentContractError("pinned runtime source bare repository is missing")
    if _path_exists(destination):
        raise DeploymentContractError("disposable run worktree destination already exists")
    _validate_private_directory(destination.parent, label="disposable run worktree parent")

    # 기대 tree는 **호출자가 materialize된 핀 소스에서 그대로 가져온 값**이다.
    # 같은 bare에서 다시 유도하면 git 결정성만 확인하는 자기참조가 되고 핀 트리와의
    # 결박이 아니다(적대 리뷰 2026-09-04 #8).
    expected_tree = _revision_output(expected_tree, label="disposable run worktree tree")
    _run_root_git(
        [
            "--git-dir",
            str(bare),
            "worktree",
            "add",
            "--detach",
            str(destination),
            source.revision,
        ],
        runner=runner,
    )
    # Git은 private parent 아래에도 worktree root를 보통 0755로 만든다. 봉인된
    # 트리와 달리 여기는 쓰기가 필요하므로 0700으로 좁히기만 한다.
    try:
        os.chmod(destination, 0o700)
    except OSError as exc:
        raise DeploymentContractError(
            "disposable run worktree cannot be secured"
        ) from exc
    _validate_private_directory(destination, label="disposable run worktree")

    revision = _revision_output(
        _run_root_git(
            ["-C", str(destination), "rev-parse", "--verify", "HEAD"],
            runner=runner,
        ).stdout,
        label="disposable run worktree revision",
    )
    if revision != source.revision:
        raise DeploymentContractError("disposable run worktree revision does not match the pin")
    tree = _revision_output(
        _run_root_git(
            ["-C", str(destination), "rev-parse", "HEAD^{tree}"],
            runner=runner,
        ).stdout,
        label="disposable run worktree tree",
    )
    if tree != expected_tree:
        raise DeploymentContractError("disposable run worktree tree does not match the pin")
    _assert_worktree_clean(target=destination, runner=runner)
    return destination


def remove_disposable_run_worktree(
    *,
    release: PinnedRuntimeRelease,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
    role: RuntimeSourceRole,
    destination: Path,
    runner: GitRunner = subprocess.run,
) -> None:
    """일회용 실행 체크아웃을 Git-owned 제거로 지운다.

    `git worktree remove`를 쓰는 이유는 admin 엔트리까지 함께 정리하기 위해서다 —
    디렉터리만 지우면 bare 저장소에 stale 엔트리가 남고, 이 저장소는
    `git worktree prune`을 운영 금지로 두고 있다.

    **디렉터리가 이미 없어도 그냥 돌아가지 않는다.** 앞선 실행이 SIGTERM/SIGHUP으로
    죽어 cleanup을 건너뛴 뒤 운영자가 경로만 지웠다면 등록만 남는데, 그 상태를 여기서
    치우지 않으면 아무도 치울 수 없다. `worktree remove --force`는 경로가 사라진
    엔트리도 지운다(2026-09-04 실측) — `prune` 없이 되돌릴 수 있다.

    성공 판정은 exit code가 아니라 **등록이 실제로 사라졌는가**로 한다.
    """

    _require_canonical_rebuildable_state_paths(
        state_paths=state_paths,
        values=values,
        release=release,
    )
    paths = pinned_runtime_source_paths(state_paths=state_paths, release=release)
    bare = paths.bare_repository(role)
    if _path_exists(destination):
        # 형제 cleanup(`_cleanup_staging_worktree`)과 같은 봉쇄를 건다 — private
        # parent 안의 현재 owner 디렉터리에만 `--force` 제거를 허용한다.
        _validate_private_directory(destination.parent, label="disposable run worktree parent")
        _reject_symlink_components(destination, label="disposable run worktree")
    try:
        _run_root_git(
            ["--git-dir", str(bare), "worktree", "remove", "--force", str(destination)],
            runner=runner,
        )
    except DeploymentContractError:
        # 등록도 경로도 이미 없으면 git은 실패하지만 결과는 우리가 원하는 상태다.
        if _is_registered_worktree(bare=bare, destination=destination, runner=runner):
            raise
        if _path_exists(destination):
            raise
    if _path_exists(destination):
        raise DeploymentContractError("disposable run worktree removal is incomplete")
    if _is_registered_worktree(bare=bare, destination=destination, runner=runner):
        raise DeploymentContractError("disposable run worktree registration is still present")


def summarize_disposable_run_worktree(
    *,
    destination: Path,
    runner: GitRunner = subprocess.run,
) -> dict[str, object]:
    """삭제 **전에** 일회용 체크아웃에 무엇이 남았는지를 증거로 만든다.

    봉인 트리를 실행에서 뺀 뒤로는, gitignore 경로(`node_modules/`, `test-results/`)
    쓰기를 관측하던 유일한 탐지기(다음 preflight의 모드 검사)가 사라진다. 여기서
    `--ignored=matching`까지 세어 두지 않으면 "실행이 무엇을 남겼는가"가 증거 없이
    삭제된다(적대 리뷰 2026-09-04 #3).

    경로 전체가 아니라 **repo-상대 최상위 이름**만 남긴다 — 진단에 필요한 만큼이고
    호스트 경로는 담지 않는다.
    """

    status = _run_root_git(
        [
            "-C",
            str(destination),
            "status",
            "--porcelain=v1",
            # **`-z`가 아니면 증거가 깨진다.** 기본 출력은 `core.quotePath` 때문에
            # 비-ASCII 경로를 8진 이스케이프로 감싸고, 초판 파서는 그것을 풀지 못해
            # 한글 파일명이 `í…`로 JSON에 실려 나갔다(pygit2 조사
            # 2026-09-04 실측). NUL 구분 출력은 경로를 인용하지도 이스케이프하지도
            # 않으므로 파싱 규칙 자체가 사라진다.
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ],
        runner=runner,
    ).stdout
    tracked = untracked = ignored = 0
    names: set[str] = set()
    records = [record for record in status.split("\0") if record]
    position = 0
    while position < len(records):
        record = records[position]
        position += 1
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        if code == "!!":
            ignored += 1
        elif code == "??":
            untracked += 1
        else:
            tracked += 1
            if code[0] in {"R", "C"}:
                # rename/copy는 **다음 레코드가 원본 경로**다. 건너뛰지 않으면 그
                # 경로를 상태 코드로 읽어 이름 집합이 오염된다.
                position += 1
        names.add(path.split("/", 1)[0])
    return {
        "tracked_changes": tracked,
        "untracked_entries": untracked,
        "ignored_entries": ignored,
        "top_level_names": sorted(names),
    }


def _is_registered_worktree(
    *,
    bare: Path,
    destination: Path,
    runner: GitRunner,
) -> bool:
    """bare가 이 경로를 worktree로 **아직 등록하고 있는지** 본다.

    경로 존재 여부와 등록 여부는 다르다. 비정상 종료 뒤에는 등록만 남는데, 그것이
    다음 `worktree add`를 죽인다.
    """

    listing = _run_root_git(
        ["--git-dir", str(bare), "worktree", "list", "--porcelain"],
        runner=runner,
    ).stdout
    wanted = str(destination)
    for line in listing.splitlines():
        if line.startswith("worktree ") and line[len("worktree ") :].strip() == wanted:
            return True
    return False


def assert_pinned_worktree_is_still_sealed(
    *,
    release: PinnedRuntimeRelease,
    state_paths: PinnedRuntimeStatePaths,
    values: Mapping[str, str],
    role: RuntimeSourceRole,
) -> None:
    """봉인된 source worktree의 **모드가** 실행 뒤에도 그대로인지 사후조건으로 본다.

    일회용 체크아웃으로 옮긴 것이 효과가 있었는지를 관측으로 만든다. 이 검사가 없으면
    "봉인 트리를 건드리지 않았다"는 주장이 다음 실행의 preflight에서야 드러나고, 그때는
    이미 한 사이클을 태운 뒤다.

    **한계를 분명히 한다.** `_validate_immutable_tree`는 모드·소유자·nlink 검사다.
    root는 0444 파일을 모드 변경 없이 덮어쓰므로 in-place 내용 변조는 잡지 못한다
    (2026-09-04 journal §1). 이 함수가 보증하는 것은 "엔트리가 추가·삭제되지 않았고
    모드가 바뀌지 않았다"까지다.
    """

    _require_canonical_rebuildable_state_paths(
        state_paths=state_paths,
        values=values,
        release=release,
    )
    paths = pinned_runtime_source_paths(state_paths=state_paths, release=release)
    source = _release_source(release, role)
    _validate_immutable_tree(paths.worktree(source))


def _release_source(
    release: PinnedRuntimeRelease, role: RuntimeSourceRole
) -> PinnedRuntimeSourceSpec:
    for source in release.sources:
        if source.role == role:
            return source
    raise DeploymentContractError("pinned runtime release has no such source role")


def _promote_staging_worktree(
    *,
    bare: Path,
    staging: Path,
    target: Path,
    runner: GitRunner,
) -> None:
    """seal 검증된 stage만 Git-owned move로 final source root에 공개한다.

    운영자가 오염된 봉인 worktree를 `rm -rf`로 지우면 **등록만 남는다.** 그 상태에서
    `worktree move`는 "missing but already registered"로 죽는데, 종전에는 그것이
    일반 Git 실패로 접혀 사유가 보이지 않았다. 여기서 먼저 판정해 조치까지 말한다
    (적대 리뷰 2026-09-04 #2 실측).
    """

    if _path_exists(target):
        raise DeploymentContractError("pinned runtime source worktree target already exists")
    if _is_registered_worktree(bare=bare, destination=target, runner=runner):
        raise DeploymentContractError(
            "pinned runtime source worktree target is registered but missing; "
            "clear it with git worktree remove --force"
        )
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
        # 위 `_root_git_environment`와 같은 이유. 사람 소유 체크아웃을 읽는 경로라
        # 특히 쓰기를 원치 않는다.
        "GIT_OPTIONAL_LOCKS": "0",
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
        # 조회가 대상 체크아웃의 index를 갱신(=쓰기)하지 않게 한다. `source_status.py`가
        # 처음부터 걸고 있던 플래그인데 이 모듈만 빠져 있었다 — 같은 계약의 두 선언이
        # 갈라진 자리다.
        #
        # **정직하게 적는다**: 이 경로에서 그 쓰기를 관측하지는 못했다. 2026-09-04에
        # 맨 셸에서는 mtime 갱신 뒤 `git status`가 index를 다시 쓰는 것을 실측했지만
        # (`GIT_OPTIONAL_LOCKS=0`이면 쓰지 않았다), `_run_root_git`을 통과하는 실제
        # 호출에서는 플래그 유무와 무관하게 index가 그대로였다. 그래서 이 항목에는
        # 동작 게이트를 붙이지 않았다 — 통과·실패가 갈리지 않는 게이트는 없느니만
        # 못하다. 남기는 이유는 방어 심층화와 두 모듈의 계약 일치다.
        "GIT_OPTIONAL_LOCKS": "0",
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
