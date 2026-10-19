"""신뢰 설치기(ADR-51 D, I-2) 계약 테스트.

설치기를 끝까지 돌리는 hermetic 테스트는 없다 — root·systemd·`/opt`가 필요하다. 최종 수락은
n150 절차(`docs/prod-deployment.md` §3)다. 여기서는 root 없이 확인할 수 있는 것만 본다:
문법, n150 헬퍼가 넘기는 인자의 호환, G lock 구간을 잘라 실제로 실행한 결과, 그리고 설치본의
실행 비트가 오는 유일한 자리인 git index.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "scripts" / "install-ktdm-trusted-release"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_INSTALLER), *args], capture_output=True, text=True, check=False
    )


def test_installer_parses() -> None:
    subprocess.run(["bash", "-n", str(_INSTALLER)], check=True)


def test_the_helper_arguments_are_accepted() -> None:
    """n150 헬퍼는 `--restart-backend --expected-source-revision SHA CLONE`을 넘긴다.

    옛 옵션이 'unknown option'으로 죽으면 새 설치기의 첫 실행이 헬퍼에서 막힌다. 인자 검사는
    root 검사보다 먼저 하므로, 틀린 revision을 주면 실행 계정과 무관하게 그 자리에서 멈춘다.
    """

    completed = _run("--restart-backend", "--allow-live", "--expected-source-revision", "x", "/tmp")

    assert completed.returncode == 126
    assert "must be a full 40-hex revision" in completed.stderr


def test_an_option_without_its_value_is_refused() -> None:
    completed = _run("--expected-source-revision")

    assert completed.returncode == 126
    assert "--expected-source-revision requires a value" in completed.stderr


def test_retired_options_are_refused() -> None:
    completed = _run("--env-file", "/opt/kor-travel-docker-manager/.env")

    assert completed.returncode == 126
    assert "unknown option: --env-file" in completed.stderr


def test_help_describes_the_usage() -> None:
    completed = _run("--help")

    assert completed.returncode == 0
    assert "usage: install-ktdm-trusted-release" in completed.stdout


def _lock_block(lock_dir: Path) -> str:
    """설치기의 `# >>> G` ~ `# <<< G` 구간을 비-root로 돌릴 수 있게 잘라 낸다.

    바꾸는 것은 소유자 지정과 uid 기대값뿐이다. umask·mode·nlink·flock은 원문 그대로 남는다.
    """

    text = _INSTALLER.read_text(encoding="utf-8")
    block = text[text.index("# >>> G") : text.index("# <<< G")]
    assert "-o root -g root " in block and '"0:600:1"' in block
    block = block.replace("-o root -g root ", "").replace(
        '"0:600:1"', f'"{os.getuid()}:600:1"'
    )
    return (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        f"LOCK_DIR={lock_dir}\n"
        'LOCK="${LOCK_DIR}/global-mutation.lock"\n'
        f"{block}\n"
        "echo HELD\n"
    )


def test_the_lock_is_created_private_under_a_permissive_umask(tmp_path: Path) -> None:
    """없던 G를 만들 때 0600이어야 한다 — 다른 획득자는 0600·nlink 1이 아니면 거부하므로,
    0644로 생기면 재부팅 전까지 Manager의 모든 mutation이 막힌다."""

    lock_dir = tmp_path / "lock"

    completed = subprocess.run(
        ["bash", "-c", "umask 002\n" + _lock_block(lock_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "HELD"
    lock = lock_dir / "global-mutation.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock_dir.stat().st_mode) == 0o700


def test_a_second_mutation_is_refused_without_waiting(tmp_path: Path) -> None:
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir(mode=0o700)
    lock = lock_dir / "global-mutation.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = subprocess.run(
            ["bash", "-c", _lock_block(lock_dir)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    finally:
        os.close(descriptor)

    assert completed.returncode == 2
    assert "another manager mutation is already active" in completed.stderr


def test_root_launchers_are_executable_in_the_git_index() -> None:
    """설치기는 archive의 실행 비트를 그대로 쓴다(ADR-51 D) — git index가 유일한 정본이다.

    옛 설치기는 archive mode를 전부 0644로 눕힌 뒤 손으로 든 목록만 0755로 되돌렸고, 그 목록에서
    빠진 `rotate-pinned-pair`가 설치본에서 조용히 실행 불가가 된 적이 있다(2026-09-02).
    """

    listed = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-s", "--", "scripts"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    modes = {
        line.split("\t", 1)[1].rsplit("/", 1)[-1]: line.split(" ", 1)[0]
        for line in listed.splitlines()
        if line
    }

    for name in (
        "install-ktdm-trusted-release",
        "run-pinned-rebuild-once",
        "run-m05-isolated-e2e-once",
        "rotate-pinned-pair",
    ):
        assert modes.get(name) == "100755", name


def test_a_symlinked_lock_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    lock_dir = tmp_path / "lock"
    lock_dir.symlink_to(real)

    completed = subprocess.run(
        ["bash", "-c", _lock_block(lock_dir)], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 126
    assert "lock directory is a symlink" in completed.stderr


def test_a_planted_fifo_is_refused_instead_of_hanging(tmp_path: Path) -> None:
    """lock 자리에 FIFO가 있으면 `>>` 열기가 영원히 막힌다 — 열기 전에 거부해야 한다."""

    lock_dir = tmp_path / "lock"
    lock_dir.mkdir(mode=0o700)
    os.mkfifo(lock_dir / "global-mutation.lock")

    completed = subprocess.run(
        ["bash", "-c", _lock_block(lock_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 126
    assert "not a regular file" in completed.stderr


def _extract_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    return text[text.index("# >>> extract") : text.index("# <<< extract")]


def test_the_release_keeps_index_exec_bits_and_drops_group_other_write(tmp_path: Path) -> None:
    """release는 `git archive`를 그대로 풀고 group/other 쓰기만 지운다(ADR-51 D).

    `tar.umask=0`과 `umask 000`으로 archive의 world-writable mode가 그대로 풀리는 최악의 경우를
    만든다 — 옛 설치기가 모든 mode를 0644로 눕혔던 이유다. 설치기의 추출 구간을 그대로 돌린다.
    """

    release = tmp_path / "release"
    release.mkdir()
    script = (
        "set -euo pipefail\n"
        "umask 000\n"
        f'git_src() {{ git -C "{_ROOT}" -c tar.umask=0 "$@" scripts deploy; }}\n'
        "SHA=HEAD\n"
        f'REL="{release}"\n' + _extract_block()
    )

    subprocess.run(["bash", "-c", script], check=True)

    extracted = list(release.rglob("*"))
    assert len(extracted) > 10
    for path in extracted:
        assert not stat.S_IMODE(path.lstat().st_mode) & 0o022, path
    for name in (
        "install-ktdm-trusted-release",
        "run-pinned-rebuild-once",
        "run-m05-isolated-e2e-once",
        "rotate-pinned-pair",
    ):
        assert stat.S_IMODE((release / "scripts" / name).stat().st_mode) == 0o755, name
    unit = release / "deploy" / "systemd" / "ktdm-backend.service"
    assert stat.S_IMODE(unit.stat().st_mode) == 0o644


def _reference_copy_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    start = text.index("# UI는 docker-compose.yml을 제자리에서 고친다.")
    end = text.index("# 모든 `.env` 쓰기는 G 아래에 있다", start)
    return text[start:end]


def test_every_install_rewrites_the_read_only_reference_compose_from_git(tmp_path: Path) -> None:
    """UI는 docker-compose.yml을 제자리에서 고친다 — C6c 보호 참조의 원본은 git에서 쓴 사본이다
    (ADR-51 결정 5). 추출 분기 **밖**에서 매번 쓴다: 같은 revision을 다시 설치하면 추출을 건너뛰므로,
    분기 안에만 있으면 빠진 사본이 영영 복구되지 않는다. UI가 고친 파일이 아니라 git에서 쓴다."""

    release = tmp_path / "release"
    release.mkdir()
    # UI가 고친 compose — 사본의 원천이 되면 안 된다.
    (release / "docker-compose.yml").write_text("services: {edited: {}}\n", encoding="utf-8")
    script = (
        "set -euo pipefail\n"
        f'git_src() {{ git -C "{_ROOT}" "$@"; }}\n'
        "SHA=HEAD\n"
        f'REL="{release}"\n' + _reference_copy_block()
    )

    for _ in range(2):  # 두 번째는 이미 있는 읽기 전용 사본을 덮어쓴다(같은 revision 재설치)
        subprocess.run(["bash", "-c", script], check=True)

    copy = release / ".ktdm-release-compose.yml"
    expected = subprocess.run(
        ["git", "-C", str(_ROOT), "show", "HEAD:docker-compose.yml"],
        capture_output=True,
        check=True,
    ).stdout
    assert copy.read_bytes() == expected
    assert stat.S_IMODE(copy.stat().st_mode) == 0o444
    assert not (release / ".ktdm-release-compose.yml.tmp").exists()


def test_the_reference_copy_is_written_outside_the_extraction_branch() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    marker = text.index('> "${REL}/.ktdm-source-revision"')
    branch_end = text.index("\nfi\n", marker)
    copy = text.index("# UI는 docker-compose.yml을 제자리에서 고친다.")
    switch = text.index('ln -sfn "${NAME}" "${APP}.new"')

    assert branch_end < copy < switch


def _preflight_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    return text[text.index("# >>> preflight") : text.index("# <<< preflight")]


def _run_preflight(
    release: Path, *, path_prefix: Path | None = None
) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        f'REL="{release}"\n' + _preflight_block() + "echo PASSED\n"
    )
    environment = dict(os.environ)
    if path_prefix is not None:
        environment["PATH"] = f"{path_prefix}:{environment['PATH']}"
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=environment
    )


def _release_with(tmp_path: Path, env_text: str) -> Path:
    release = tmp_path / "release"
    release.mkdir()
    (release / "docker-compose.yml").write_text(
        "name: preflight-probe\n"
        "x-url: &url\n"
        "  URL: postgresql://app:${PREFLIGHT_NEEDED:?set PREFLIGHT_NEEDED in .env}@h/db\n"
        "services:\n"
        "  one:\n"
        "    image: busybox\n"
        "    environment:\n"
        "      <<: *url\n"
        "      OTHER: ${PREFLIGHT_OTHER:-x}\n",
        encoding="utf-8",
    )
    (release / ".env").write_text(env_text, encoding="utf-8")
    return release


def _compose_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"], capture_output=True, check=False, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _compose_available(), reason="docker compose가 없다")
def test_the_preflight_refuses_a_release_whose_compose_the_env_cannot_resolve(
    tmp_path: Path,
) -> None:
    """새 `${X:?}`가 `.env`에 없으면 flip 전에 멈춘다 — 넘기면 모든 compose 명령이 죽는다(M1)."""

    missing = _run_preflight(_release_with(tmp_path, "PREFLIGHT_OTHER=hunter2\n"))

    assert missing.returncode == 126, missing.stderr
    assert "not flipping" in missing.stderr
    assert "PREFLIGHT_NEEDED" in missing.stderr
    assert "PASSED" not in missing.stdout


@pytest.mark.skipif(not _compose_available(), reason="docker compose가 없다")
def test_the_preflight_passes_when_the_env_resolves_the_compose(tmp_path: Path) -> None:
    present = _run_preflight(_release_with(tmp_path, "PREFLIGHT_NEEDED=value\n"))

    assert present.returncode == 0, present.stderr
    assert present.stdout.strip() == "PASSED"


def test_the_preflight_names_only_compose_variables_never_values(tmp_path: Path) -> None:
    """compose 오류 문구에 값이 섞여도 설치기 stderr에는 compose가 참조하는 변수 이름만 남는다."""

    release = _release_with(tmp_path, "PREFLIGHT_NEEDED=s3cr3t-value\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        "echo 'error: PREFLIGHT_NEEDED resolved to s3cr3t-value near UNRELATED_WORD' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    refused = _run_preflight(release, path_prefix=fake_bin)

    assert refused.returncode == 126
    assert "PREFLIGHT_NEEDED" in refused.stderr
    assert "s3cr3t" not in refused.stderr
    assert "UNRELATED_WORD" not in refused.stderr


def test_the_preflight_runs_after_the_env_copy_and_before_anything_is_flipped() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    env_copy = text.index('cp -p -- "${APP}/.env" "${REL}/.env"')
    preflight = text.index("# >>> preflight")
    first_host_write = text.index("install -T -o root -g root")
    flip = text.index('ln -sfn "${NAME}" "${APP}.new"')

    assert env_copy < preflight < first_host_write < flip
    assert "--env-file \"${REL}/.env\" config --quiet" in _preflight_block()


# --- 2026-10-16 T-MGR-01a-F (1): flip 전 자기 식별 ---------------------------------------------
# smoke import는 config를 로드하지 않아 `KTDM_HOST` 누락·비로컬 주소가 flip **뒤에야** 모든 ktdctl·backend 조회를
# 죽였다(#477 리뷰 차단 2번 후속). 새 release의 `targets validate --json`을 `.env`만으로 돌려 flip 전에 멈춘다.


def _host_identity_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    return text[text.index("# >>> host-identity") : text.index("# <<< host-identity")]


def test_host_identity_runs_after_the_env_copy_and_before_anything_is_flipped() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    env_copy = text.index('cp -p -- "${APP}/.env" "${REL}/.env"')
    marker = text.index('> "${REL}/.ktdm-source-revision"')
    identity = text.index("# >>> host-identity")
    first_host_write = text.index("install -T -o root -g root")
    flip = text.index('ln -sfn "${NAME}" "${APP}.new"')

    # 추출 분기 밖이다 — 같은 sha 재설치(.env만 바뀐 경우)도 다시 본다.
    assert marker < text.index("\nfi\n", marker) < env_copy < identity < first_host_write < flip
    assert '"targets", "validate", "--json"' in _host_identity_block()
    assert "-u KTDM_HOST" in _host_identity_block()


def _identity_release(tmp_path: Path, env_text: str | None) -> Path:
    """설치기가 만든 release의 최소 모양: 표식·config·`.env`·release venv의 python."""

    import sys

    import yaml

    from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

    release = tmp_path / "release"
    (release / "config").mkdir(parents=True)
    config = load_yaml_rejecting_duplicate_keys(
        (_ROOT / "config" / "docker-targets.yml").read_text(encoding="utf-8")
    )
    # 이 장비의 실제 인터페이스로 판정하게 한다: default 호스트는 loopback(항상 로컬), 나머지는 TEST-NET.
    for index, spec in enumerate(config["hosts"].values()):
        spec["addr"] = "127.0.0.1" if spec.get("default") else f"192.0.2.{index + 1}"
    (release / "config" / "docker-targets.yml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (release / ".ktdm-source-revision").write_text("a" * 40 + "\n", encoding="utf-8")
    if env_text is not None:
        (release / ".env").write_text(env_text, encoding="utf-8")
    python = release / "backend" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return release


def _run_identity(release: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        f'REL="{release}"\n' + _host_identity_block() + "echo PASSED\n"
    )
    environment = {**os.environ, **extra_env}
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, env=environment
    )


def _hosts() -> tuple[str, str]:
    from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

    hosts = load_yaml_rejecting_duplicate_keys(
        (_ROOT / "config" / "docker-targets.yml").read_text(encoding="utf-8")
    )["hosts"]
    default = next(name for name, spec in hosts.items() if spec.get("default"))
    other = next(name for name in hosts if name != default)
    return default, other


#: 설치기 식별 테스트는 실제 인터페이스로 판정한다. `/usr/sbin/ip`가 없는 곳(Windows·macOS 개발 PC)에서는 skip — CI
#: (ubuntu)와 n150에는 있다.
_HAS_IP = Path("/usr/sbin/ip").exists()


@pytest.mark.skipif(not _HAS_IP, reason="/usr/sbin/ip 없음(Linux 전용)")
def test_host_identity_passes_for_the_local_host(tmp_path: Path) -> None:
    default, _ = _hosts()
    completed = _run_identity(_identity_release(tmp_path, f"OTHER=x\nKTDM_HOST={default}\n"))

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "PASSED"


@pytest.mark.skipif(not _HAS_IP, reason="/usr/sbin/ip 없음(Linux 전용)")
@pytest.mark.parametrize(
    ("env_text", "reason"),
    [
        (None, "requires hosts and KTDM_HOST"),
        ("KTDM_HOST=\n", "requires hosts and KTDM_HOST"),
        ("KTDM_HOST=mars\n", "KTDM_HOST must be a declared hosts key"),
        ("KTDM_HOST={other}\n", "KTDM_HOST address is not assigned to this host"),
    ],
)
def test_host_identity_refuses_before_the_flip(tmp_path: Path, env_text: str | None, reason: str) -> None:
    default, other = _hosts()
    release = _identity_release(tmp_path, None if env_text is None else env_text.format(other=other))
    # 운영자 셸의 KTDM_HOST가 `.env`의 공백을 메우지 못한다 — backend 유닛은 `.env`만 본다.
    completed = _run_identity(release, KTDM_HOST=default)

    assert completed.returncode == 126, completed.stdout
    assert "not flipping" in completed.stderr
    assert reason in completed.stderr
    assert "PASSED" not in completed.stdout


def _local_ipv4() -> list[str]:
    import json

    data = json.loads(
        subprocess.run(["/usr/sbin/ip", "-j", "-4", "address", "show"], capture_output=True, check=True).stdout
    )
    return sorted({a["local"] for i in data for a in i.get("addr_info", []) if a.get("family") == "inet"})


@pytest.mark.skipif(not _HAS_IP, reason="/usr/sbin/ip 없음(Linux 전용)")
def test_host_identity_refuses_two_local_declared_addresses(tmp_path: Path) -> None:
    """선언 주소 둘이 이 장비에 있으면(보조 IPv4·VIP) 어느 이름이든 식별이 모호하다 — flip 전에 멈춘다."""

    import yaml

    local = _local_ipv4()
    # 이 장비에 IPv4가 둘 이상 있어야 "선언 주소 둘이 로컬"을 실제로 만들 수 있다(loopback + LAN). 하나뿐이면 skip.
    if len(local) < 2:
        pytest.skip("이 장비의 IPv4가 둘 미만이다")
    default, other = _hosts()
    release = _identity_release(tmp_path, f"KTDM_HOST={default}\n")
    path = release / "config" / "docker-targets.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["hosts"][default]["addr"], config["hosts"][other]["addr"] = local[0], local[1]
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    completed = _run_identity(release)

    assert completed.returncode == 126, completed.stdout
    assert "not flipping" in completed.stderr
    assert "more than one declared host address is assigned to this host" in completed.stderr


@pytest.mark.skipif(not _HAS_IP, reason="/usr/sbin/ip 없음(Linux 전용)")
def test_the_pre_flip_validate_names_every_consumer_origin_override(tmp_path: Path) -> None:
    """T-MGR-01c: 같은 flip 전 `targets validate`가 root `.env`의 소비자 origin 재정의도 거부한다. 문구는 식별 실패가 아니라
    중립이고, 위반 줄을 **전부** 낸다(마지막 줄만 남기면 둘째 위반이 가려진다)."""

    default, _ = _hosts()
    variables = ["KOR_TRAVEL_MAP_DOCKER_GEO_BASE_URL", "PINVI_KOR_TRAVEL_WEATHER_BASE_URL"]
    release = _identity_release(
        tmp_path, f"KTDM_HOST={default}\n" + "".join(f"{v}=http://10.9.9.9:1\n" for v in variables)
    )
    (release / "docker-compose.yml").write_bytes((_ROOT / "docker-compose.yml").read_bytes())

    completed = _run_identity(release)

    assert completed.returncode == 126, completed.stdout
    assert "targets validate failed" in completed.stderr and "not flipping" in completed.stderr
    assert "identify this host" not in completed.stderr
    assert [v for v in variables if v not in completed.stderr] == []
    assert "10.9.9.9" not in completed.stderr  # 재정의된 값은 싣지 않는다


# --- 2026-10-17 T-MGR-NH NH-4 리뷰 OS-1: 떠 있는 plane의 workspace bind 파일이 새 release에 있어야 한다 -----------


def _plane_binds_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    return text[text.index("# >>> plane-binds") : text.index("# <<< plane-binds")]


def test_plane_binds_runs_before_anything_is_flipped() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    assert text.index("# <<< host-identity") < text.index("# >>> plane-binds") < text.index("install -T -o root -g root")
    assert text.index("# >>> plane-binds") < text.index('ln -sfn "${NAME}" "${APP}.new"')


def _run_plane_binds(
    tmp_path: Path,
    mounted: str,
    shipped: list[str],
    root: str | None = None,
    info_exit: int = 0,
    current_ships: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """가짜 docker: `info`는 `info_exit`, `ps -aq`는 컨테이너 하나(`-a` 없이 부르면 빈 목록 — 멈춘 plane을 놓친다),
    `inspect`는 그 workspace bind source 한 줄."""
    release = tmp_path / "release"
    (release / "config" / "dagster-shared").mkdir(parents=True, exist_ok=True)
    for name in shipped:
        (release / "config" / "dagster-shared" / name).write_text("load_from: []\n", encoding="utf-8")
    # 지금 설치된 release(APP) — 전환 판정이 본다.
    current = tmp_path / "current"
    (current / "config" / "dagster-shared").mkdir(parents=True, exist_ok=True)
    for name in current_ships:
        (current / "config" / "dagster-shared" / name).write_text("load_from: []\n", encoding="utf-8")
    root = str(current) if root is None else root
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  info) exit {info_exit} ;;\n"
        '  ps) [ "$2" = "-aq" ] && printf "gone\\nabc123\\n" ;;\n'
        f'  inspect) [ "$4" = gone ] && exit 1; echo {root}/config/dagster-shared/{mounted} ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        f'REL="{release}"\nAPP={current}\nplane_rebind=""\n' + _plane_binds_block()
        + 'echo "PASSED rebind=${plane_rebind}"\n'
    )
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=environment)


def test_plane_binds_refuses_a_workspace_the_new_release_does_not_ship(tmp_path: Path) -> None:
    refused = _run_plane_binds(tmp_path, "workspace.yaml", ["workspace.n150.yaml", "workspace.odroid.yaml"])
    assert refused.returncode == 126 and "PASSED" not in refused.stdout
    assert "binds config/dagster-shared/workspace.yaml," in refused.stderr
    assert "dagster-plane converge --dry-run" in refused.stderr and "--confirm" in refused.stderr


def test_plane_binds_passes_a_host_workspace_the_new_release_ships(tmp_path: Path) -> None:
    passed = _run_plane_binds(tmp_path, "workspace.n150.yaml", ["workspace.n150.yaml", "workspace.odroid.yaml"])
    assert passed.returncode == 0 and "PASSED" in passed.stdout, passed.stderr


def test_plane_binds_ignores_planes_outside_this_install_root(tmp_path: Path) -> None:
    # 같은 호스트의 격리 테스트 plane(다른 체크아웃의 bind)은 이 release와 무관하다.
    ignored = _run_plane_binds(tmp_path, "workspace.yaml", ["workspace.n150.yaml"], root="/home/someone/checkout")
    assert ignored.returncode == 0 and "PASSED" in ignored.stdout, ignored.stderr


def test_plane_binds_compares_the_path_relative_to_the_install_root(tmp_path: Path) -> None:
    # basename이 아니라 설치 root 기준 상대경로로 대조한다 — 같은 이름이 다른 디렉터리에 있어도 통과하지 않는다.
    release = tmp_path / "release"
    (release / "config" / "elsewhere").mkdir(parents=True)
    (release / "config" / "elsewhere" / "workspace.yaml").write_text("load_from: []\n", encoding="utf-8")
    refused = _run_plane_binds(tmp_path, "workspace.yaml", [])
    assert refused.returncode == 126 and "binds config/dagster-shared/workspace.yaml," in refused.stderr


def test_plane_binds_refuses_when_docker_does_not_answer(tmp_path: Path) -> None:
    refused = _run_plane_binds(tmp_path, "workspace.n150.yaml", ["workspace.n150.yaml"], info_exit=1)
    assert refused.returncode == 126 and "docker daemon did not answer" in refused.stderr


def test_plane_binds_lets_the_one_alias_transition_through_and_says_to_converge(tmp_path: Path) -> None:
    # 지금 설치된 release에는 그 파일이 있다(옛 별칭) — 옛 converge는 bind 차이를 못 봐서 설치 전에 고칠 길이 없다. 거부하지 않고
    # 기록하며, 새 release의 converge를 바로 돌리라고 말한다.
    passed = _run_plane_binds(
        tmp_path, "workspace.yaml", ["workspace.n150.yaml"], current_ships=("workspace.yaml", "workspace.n150.yaml")
    )
    assert passed.returncode == 0, passed.stderr
    assert "PASSED rebind=config/dagster-shared/workspace.yaml" in passed.stdout
    assert "dagster-plane converge --dry-run" in passed.stderr and "right after this install" in passed.stderr


def test_the_install_success_message_names_the_pending_converge() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    installed = text.index('echo "installed ${SHA} (previous: ${PREV#ktdm-release-})"')
    reminder = text.index('NEXT: the shared Dagster plane still binds ${plane_rebind}')
    cleanup = text.index('rm -rf -- "${old}"', installed)
    # 성공 경로의 stdout 마지막 줄: 옛 release 정리 뒤, `exit 0` 바로 앞, stderr가 아니다.
    assert installed < cleanup < reminder < text.index("exit 0", installed)
    line = text[text.rindex("\n", 0, reminder) + 1 : text.index("\n", reminder)]
    assert ">&2" not in line
    assert text.index('plane_rebind=""') < text.index("# >>> plane-binds")


def test_plane_binds_refuses_a_file_neither_release_ships(tmp_path: Path) -> None:
    refused = _run_plane_binds(tmp_path, "workspace.yaml", ["workspace.n150.yaml"], current_ships=("workspace.n150.yaml",))
    assert refused.returncode == 126 and "PASSED" not in refused.stdout
    assert (
        "running Dagster plane binds config/dagster-shared/workspace.yaml, which neither the current nor the new release ships"
        in refused.stderr
    )


def test_plane_binds_refuses_an_empty_directory_left_in_the_current_release(tmp_path: Path) -> None:
    # docker가 없는 bind source를 디렉터리로 만들어 둔 경우 — 파일이 아니므로 전환 허용 대상이 아니다.
    (tmp_path / "current" / "config" / "dagster-shared" / "workspace.yaml").mkdir(parents=True)
    refused = _run_plane_binds(tmp_path, "workspace.yaml", ["workspace.n150.yaml"])
    assert refused.returncode == 126 and "which neither the current nor the new release ships" in refused.stderr


# --- 2026-10-17: release root를 설치 root symlink에서 정한다(데이터 디스크에 release를 두는 호스트) ---------------


def _release_root_block() -> str:
    text = _INSTALLER.read_text(encoding="utf-8")
    return text[text.index("# >>> release-root") : text.index("# <<< release-root")]


def _run_release_root(tmp_path: Path, target: str, *, app_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    """release-root 구획만 돌린다. `stat` shim이 `tmp_path` 밖은 root 소유·0755로, 안은 root 소유·실제 mode로 보여 준다 —
    테스트는 root가 아니라서 소유자는 흉내 내고, mode(group/other 쓰기)·symlink·정규화 검사는 실제 파일로 본다."""
    app = (app_dir or (tmp_path / "opt")) / "kor-travel-docker-manager"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.symlink_to(target)
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    real_stat = subprocess.run(["bash", "-c", "command -v stat"], capture_output=True, text=True).stdout.strip()
    (shim / "stat").write_text(
        "#!/bin/bash\n"
        'fmt="$2"; path="$3"\n'
        'if [[ "$fmt" == "%u" ]]; then echo 0; exit 0; fi\n'
        f'if [[ "$path" == "{tmp_path}"* ]]; then exec {real_stat} "$@"; fi\n'
        "echo 755\n",
        encoding="utf-8",
    )
    (shim / "stat").chmod(0o755)
    script = (
        "set -euo pipefail\n"
        'die() { echo "$1" >&2; exit 126; }\n'
        f'export PATH="{shim}:$PATH"\n'
        f'APP="{app}"\n' + _release_root_block() + 'echo "ROOT=${RELEASE_ROOT}"\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def _trusted(tmp_path: Path, *parts: str) -> Path:
    path = tmp_path.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    for node in [tmp_path, *path.relative_to(tmp_path).parents, path.relative_to(tmp_path)]:
        (tmp_path / node).chmod(0o755)
    return path


def test_an_absolute_symlink_puts_releases_under_its_parent(tmp_path: Path) -> None:
    root = _trusted(tmp_path, "srv", "ktdm-releases")
    _trusted(tmp_path, "opt")
    passed = _run_release_root(tmp_path, f"{root}/ktdm-release-" + "a" * 40)
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.splitlines()[-1] == f"ROOT={root}"


def test_a_group_writable_release_root_is_refused(tmp_path: Path) -> None:
    root = _trusted(tmp_path, "srv", "ktdm-releases")
    _trusted(tmp_path, "opt")
    root.chmod(0o775)
    refused = _run_release_root(tmp_path, f"{root}/ktdm-release-" + "a" * 40)
    assert refused.returncode == 126 and "release root must be a root-owned directory chain" in refused.stderr


def test_a_symlink_inside_the_release_root_path_is_refused(tmp_path: Path) -> None:
    real = _trusted(tmp_path, "data", "releases")
    _trusted(tmp_path, "opt")
    (tmp_path / "srv").symlink_to(real.parent)
    refused = _run_release_root(tmp_path, f"{tmp_path}/srv/releases/ktdm-release-" + "a" * 40)
    assert refused.returncode == 126 and "release root must be a canonical directory" in refused.stderr


@pytest.mark.parametrize("shape", ["srv/ktdm-releases/../ktdm-releases", "srv/./ktdm-releases", "srv//ktdm-releases"])
def test_an_uncanonical_release_root_is_refused(tmp_path: Path, shape: str) -> None:
    _trusted(tmp_path, "srv", "ktdm-releases")
    _trusted(tmp_path, "opt")
    refused = _run_release_root(tmp_path, f"{tmp_path}/{shape}/ktdm-release-" + "a" * 40)
    assert refused.returncode == 126 and "release root must be a canonical directory" in refused.stderr, refused.stdout


def test_the_filesystem_root_is_not_a_release_root(tmp_path: Path) -> None:
    _trusted(tmp_path, "opt")
    refused = _run_release_root(tmp_path, "/ktdm-release-" + "a" * 40)
    assert refused.returncode == 126 and "other than /" in refused.stderr


def test_a_symlink_target_with_unexpected_characters_is_refused(tmp_path: Path) -> None:
    root = _trusted(tmp_path, "srv", "ktdm-releases")
    _trusted(tmp_path, "opt")
    refused = _run_release_root(tmp_path, f"{root}/ktdm-release-a b")
    assert refused.returncode == 126 and "unexpected characters" in refused.stderr


def test_the_install_root_directory_is_checked_too(tmp_path: Path) -> None:
    root = _trusted(tmp_path, "srv", "ktdm-releases")
    opt = _trusted(tmp_path, "opt")
    opt.chmod(0o777)
    refused = _run_release_root(tmp_path, f"{root}/ktdm-release-" + "a" * 40)
    assert refused.returncode == 126 and "install root directory must be a root-owned directory chain" in refused.stderr


def test_a_relative_symlink_keeps_the_release_root_next_to_the_install_root(tmp_path: Path) -> None:
    # 종전 동작: 상대 symlink면 설치 root와 같은 디렉터리.
    opt = _trusted(tmp_path, "opt")
    passed = _run_release_root(tmp_path, "ktdm-release-" + "a" * 40)
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.splitlines()[-1] == f"ROOT={opt}"


def test_the_flip_keeps_relative_links_for_opt_and_absolute_links_elsewhere() -> None:
    text = _INSTALLER.read_text(encoding="utf-8")
    flip = text[text.index('if [[ "${RELEASE_ROOT}" == "$(dirname -- "${APP}")" ]]; then') :]
    assert flip.index('ln -sfn "${NAME}" "${APP}.new"') < flip.index('ln -sfn "${REL}" "${APP}.new"')
    # GC는 release root 안에서만 돈다 — 그 밖의 경로를 지우는 줄이 없다.
    assert 'for old in "${RELEASE_ROOT}"/ktdm-release-*; do' in text
    assert '[[ "${old}" == "${REL}" || "${old}" == "${RELEASE_ROOT}/${PREV}" ]]' in text
    assert "/opt/ktdm-release-*" not in text
    assert [line for line in text.splitlines() if "rm -rf --" in line] == [
        '  rm -rf -- "${REL}"',
        '          || { rm -f -- "${old}/.ktdm-source-revision"; rm -rf -- "${old}"; }',
    ]


def _installer_section(name: str) -> str:
    text = _INSTALLER.read_text()
    return text[text.index(f"# >>> {name}"):text.index(f"# <<< {name}")]


@pytest.mark.parametrize("load,active,pid,accepted", [
    ("loaded", "inactive", "0", True),
    ("not-found", "inactive", "0", True),
    ("loaded", "active", "123", False),
    ("loaded", "activating", "0", False),
    ("loaded", "inactive", "123", False),
    ("loaded", "failed", "0", False),
])
def test_cli_only_requires_both_services_already_stopped(load, active, pid, accepted):
    script = "set -euo pipefail\ndie() { echo \"$1\" >&2; exit 126; }\n"
    script += f"systemctl() {{ printf '%s\\n' LoadState={load} ActiveState={active} MainPID={pid}; }}\n"
    script += _installer_section("cli-service-state") + "\nrequire_cli_services_stopped\n"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert (result.returncode == 0) is accepted


@pytest.mark.parametrize("exit_code", [0, 1])
def test_cli_only_finishes_by_cli_validation_without_service_start(tmp_path, exit_code):
    executable = tmp_path / "backend/.venv/bin/ktdctl"
    executable.parent.mkdir(parents=True)
    executable.write_text(f"#!/bin/bash\n[[ \"$*\" == 'targets validate --json' ]] || exit 99\nexit {exit_code}\n")
    executable.chmod(0o700)
    script = f"set -euo pipefail\nAPP={tmp_path}\nPREV=ktdm-release-old\nSRC=/owned-source\nCLI_ONLY=true\n"
    script += "finish_install() { echo FINISHED; }\nsystemctl() { exit 98; }\n"
    script += _installer_section("cli-finish")
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == exit_code
    assert ("FINISHED" in result.stdout) is (exit_code == 0)
    if exit_code:
        assert "--cli-only" in result.stderr


def test_cli_only_argument_is_accepted_before_host_access():
    result = _run("--cli-only", "--expected-source-revision", "x")
    assert result.returncode == 126
    assert "must be a full 40-hex revision" in result.stderr


def test_cli_only_excludes_host_files_and_unit_mutations():
    text = _INSTALLER.read_text()
    start = text.index("if ! ${CLI_ONLY}; then\n# host 파일")
    end = text.index('systemctl enable "${units[@]}"\nfi', start)
    block = text[start:end]
    assert "systemd-tmpfiles --create" in block
    assert "/etc/systemd/system/ktdm-backend.service" in block
    assert "/etc/systemd/system/ktdm-frontend.service" in block
    assert "/etc/logrotate.d/kor-travel-docker-manager" in block
    assert "systemctl daemon-reload" in block
    assert text.index("# <<< cli-finish") < text.index("systemctl reset-failed")
    assert text.count("require_cli_services_stopped") == 3
