"""scripts/n150-live-e2e.sh의 자격 파일 검사·값 비노출·인자 제한 계약.

sudo·stat·docker·gate를 가짜로 바꿔 root 없이 돈다(실제 브라우저·운영 접속 없음).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "n150-live-e2e.sh"
PASSWORD = "pw-Zq8!value with space"
VALID = (
    "# Manager e2e\n"
    "KTDM_LIVE_WEB_URL=https://manager.example.test/\n"
    "KTDM_LIVE_USERNAME='admin'\n"
    f"KTDM_LIVE_PASSWORD='{PASSWORD}'\n"
)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash가 필요하다")


def _exe(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def box(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # sudo: 권한 상승 없이 인자만 실행한다(가짜 stat이 PATH 앞에 남는다).
    _exe(bin_dir / "sudo", 'while [[ "$1" == -n || "$1" == -- ]]; do shift; done\n[[ "$1" == true ]] && exit 0\nexec "$@"')
    _exe(bin_dir / "stat", 'if [[ -d "${@: -1}" ]]; then echo "${FAKE_DIR_META:-0 700}"; else echo "${FAKE_FILE_META:-0 600}"; fi')
    _exe(
        bin_dir / "docker",
        'printf "%s\\n" "$@" > "$FAKE_DOCKER_ARGS"\n'
        'printf "%s\\n" "KTDM_LIVE_WEB_URL=$KTDM_LIVE_WEB_URL" "KTDM_LIVE_USERNAME=$KTDM_LIVE_USERNAME" '
        '"KTDM_LIVE_PASSWORD=$KTDM_LIVE_PASSWORD" "KTDM_LIVE_EXPECT_HOST=$KTDM_LIVE_EXPECT_HOST" '
        '"KTDM_LIVE_E2E_WRITE=${KTDM_LIVE_E2E_WRITE:-}" > "$FAKE_DOCKER_ENV"',
    )
    _exe(bin_dir / "gate", 'exec "$@"')
    secret = tmp_path / "secret"
    secret.mkdir()
    return {
        "root": tmp_path, "bin": bin_dir, "cred": secret / "ktdm-e2e.env",
        "args": tmp_path / "docker-args", "env": tmp_path / "docker-env",
    }


def _run(box: dict[str, Path], *args: str, env: dict[str, str] | None = None,
         bash_flags: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    full_env = {
        "PATH": f"{box['bin']}:/usr/bin:/bin", "HOME": str(box["root"]),
        "KTDM_LIVE_E2E_CREDENTIALS": str(box["cred"]), "KTDM_LIVE_E2E_GATE": str(box["bin"] / "gate"),
        "KTDM_LIVE_E2E_DOCKER": str(box["bin"] / "docker"),
        "FAKE_DOCKER_ARGS": str(box["args"]), "FAKE_DOCKER_ENV": str(box["env"]),
        **(env or {}),
    }
    result = subprocess.run(
        ["bash", *bash_flags, str(SCRIPT), *args],
        capture_output=True, text=True, env=full_env, check=False,
    )
    assert PASSWORD not in result.stdout + result.stderr, "자격 값이 출력에 나왔습니다"
    return result


def test_xtrace_is_refused_before_reading_credentials(box):
    box["cred"].write_text(VALID, encoding="utf-8")
    result = _run(box, bash_flags=("-x",))
    assert result.returncode == 2
    assert "xtrace" in result.stderr
    assert not box["args"].exists()


@pytest.mark.parametrize("arg", ["--config", "-c", "--reporter=line", "--workers=4", "--project=all", "x.spec.ts"])
def test_config_reporter_and_free_args_are_refused(box, arg):
    box["cred"].write_text(VALID, encoding="utf-8")
    result = _run(box, arg, "x")
    assert result.returncode == 2
    assert "허용하지 않는" in result.stderr or "--project는" in result.stderr
    assert not box["args"].exists()


@pytest.mark.parametrize(
    ("env", "code", "message"),
    [
        ({"FAKE_FILE_META": "0 644"}, 4, "root 소유·0600"),
        ({"FAKE_FILE_META": "1000 600"}, 4, "root 소유·0600"),
        ({"FAKE_DIR_META": "1000 700"}, 4, "디렉터리는 root 소유"),
        ({"FAKE_DIR_META": "0 777"}, 4, "group·other 쓰기"),
    ],
)
def test_credential_file_must_be_root_only(box, env, code, message):
    box["cred"].write_text(VALID, encoding="utf-8")
    result = _run(box, env=env)
    assert result.returncode == code
    assert message in result.stderr
    assert not box["args"].exists()


def test_symlink_and_missing_credentials_are_refused(box):
    box["cred"].write_text(VALID, encoding="utf-8")
    link = box["cred"].parent / "link.env"
    os.symlink(box["cred"], link)
    linked = _run(box, env={"KTDM_LIVE_E2E_CREDENTIALS": str(link)})
    assert linked.returncode == 3
    assert "일반 파일이 아닙니다" in linked.stderr
    missing = _run(box, env={"KTDM_LIVE_E2E_CREDENTIALS": str(box["cred"].parent / "none.env")})
    assert missing.returncode == 3
    relative = _run(box, env={"KTDM_LIVE_E2E_CREDENTIALS": "ktdm-e2e.env"})
    assert relative.returncode == 2
    assert not box["args"].exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (f"KTDM_ADMIN_PASSWORD_HASH={PASSWORD}\n", "5행의 키 KTDM_ADMIN_PASSWORD_HASH는 허용하지 않습니다"),
        (f"{PASSWORD}\n", "5행이 KEY=VALUE 형식이 아닙니다"),
        (f"KTDM_LIVE_PASSWORD={PASSWORD}\n", "KTDM_LIVE_PASSWORD가 두 번"),
    ],
)
def test_bad_lines_report_line_number_without_value(box, extra, message):
    box["cred"].write_text(VALID + extra, encoding="utf-8")
    result = _run(box)
    assert result.returncode == 2
    assert message in result.stderr
    assert not box["args"].exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("KTDM_LIVE_USERNAME=admin\nKTDM_LIVE_PASSWORD=x1234\n", "KTDM_LIVE_WEB_URL가 없습니다"),
        (VALID.replace("https://manager.example.test/", "https://manager.example.test/login"), "경로 없이"),
        (VALID.replace(f"KTDM_LIVE_PASSWORD='{PASSWORD}'\n", ""), "KTDM_LIVE_PASSWORD가 없습니다"),
        (VALID.replace("https://manager.example.test/", "http://manager.example.test"), "loopback이 아니면 https"),
    ],
)
def test_required_keys_and_url_shape(box, content, message):
    box["cred"].write_text(content, encoding="utf-8")
    result = _run(box)
    assert result.returncode == 2
    assert message in result.stderr


def test_valid_credentials_pass_names_only_and_fixed_read_only_config(box):
    box["cred"].write_text("export " + VALID.split("\n", 1)[1], encoding="utf-8")
    result = _run(box, "--project", "mobile", "--grep", "수집 경보")
    assert result.returncode == 0, result.stderr
    argv = box["args"].read_text(encoding="utf-8").splitlines()
    assert PASSWORD not in "\n".join(argv)
    pairs = list(zip(argv, argv[1:], strict=False))
    for name in ("KTDM_LIVE_WEB_URL", "KTDM_LIVE_USERNAME", "KTDM_LIVE_PASSWORD", "KTDM_LIVE_EXPECT_HOST"):
        assert ("--env", name) in pairs
    assert "LIVE_E2E_CONFIG=playwright.live.config.ts" in argv
    assert "LIVE_E2E_REDACT=KTDM_LIVE_PASSWORD" in argv
    assert ("--project", "mobile") in pairs and ("--grep", "수집 경보") in pairs
    assert any(item.startswith("mcr.microsoft.com/playwright:v") and item.endswith("-noble") for item in argv)
    # 컨테이너에는 frontend/만 싣는다.
    assert f"{ROOT / 'frontend'}:/work" in argv
    assert not any(item.startswith(f"{ROOT}:") for item in argv)
    assert "KTDM_LIVE_E2E_WRITE" not in argv
    assert "PLAYWRIGHT_NO_COPY_PROMPT=1" in argv
    assert "LIVE_E2E_SECRET_VARS=KTDM_LIVE_PASSWORD,KTDM_LIVE_USERNAME" in argv
    assert "--shm-size=1g" in argv and "--ipc=host" not in argv
    env = box["env"].read_text(encoding="utf-8").splitlines()
    assert "KTDM_LIVE_WEB_URL=https://manager.example.test" in env
    assert "KTDM_LIVE_USERNAME=admin" in env
    assert f"KTDM_LIVE_PASSWORD={PASSWORD}" in env
    assert "KTDM_LIVE_EXPECT_HOST=n150" in env
    assert "KTDM_LIVE_E2E_WRITE=" in env
    assert "값은 출력하지 않습니다" in result.stdout


def test_write_specs_need_explicit_opt_in(box):
    box["cred"].write_text(VALID, encoding="utf-8")
    result = _run(box, env={"KTDM_LIVE_E2E_WRITE": "1", "KTDM_LIVE_EXPECT_HOST": "odroid"})
    assert result.returncode == 0, result.stderr
    argv = box["args"].read_text(encoding="utf-8").splitlines()
    assert ("--env", "KTDM_LIVE_E2E_WRITE") in list(zip(argv, argv[1:], strict=False))
    env = box["env"].read_text(encoding="utf-8").splitlines()
    assert "KTDM_LIVE_E2E_WRITE=1" in env and "KTDM_LIVE_EXPECT_HOST=odroid" in env
    assert _run(box, env={"KTDM_LIVE_E2E_WRITE": "yes"}).returncode == 2
    assert _run(box, env={"KTDM_LIVE_EXPECT_HOST": "N150;rm"}).returncode == 2


def test_missing_gate_is_refused(box):
    box["cred"].write_text(VALID, encoding="utf-8")
    result = _run(box, env={"KTDM_LIVE_E2E_GATE": str(box["root"] / "missing-gate.sh")})
    assert result.returncode == 2
    assert "test gate" in result.stderr
    assert not box["args"].exists()


def test_live_config_keeps_write_specs_out_by_default():
    config = (ROOT / "frontend" / "playwright.live.config.ts").read_text(encoding="utf-8")
    assert "KTDM_LIVE_E2E_WRITE === '1' ? [] : ['**/*.write.live.spec.ts']" in config
    assert "trace: 'off'" in config
    assert "globalSetup: './e2e-live/login-preflight.ts'" in config
    assert "serviceWorkers: 'block'" in config
    fixtures = (ROOT / "frontend" / "e2e-live" / "live-fixtures.ts").read_text(encoding="utf-8")
    assert "context.route('**/*'" in fixtures  # 새 탭·popup도 같은 변경 차단
    for spec in (ROOT / "frontend" / "e2e-live").glob("*.live.spec.ts"):
        assert "from './live-fixtures'" in spec.read_text(encoding="utf-8"), spec.name
    default = (ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")
    assert "testDir: './e2e'" in default  # CI 격리 회귀는 e2e-live를 수집하지 않는다


def test_symlinked_parent_and_missing_docker_are_refused(box):
    box["cred"].write_text(VALID, encoding="utf-8")
    linked = box["root"] / "linked"
    os.symlink(box["cred"].parent, linked)
    via_link = _run(box, env={"KTDM_LIVE_E2E_CREDENTIALS": str(linked / "ktdm-e2e.env")})
    assert via_link.returncode == 3
    assert "심볼릭 링크" in via_link.stderr
    no_docker = _run(box, env={"KTDM_LIVE_E2E_DOCKER": str(box["root"] / "no-docker")})
    assert no_docker.returncode == 2
    assert "docker 실행 파일이 없습니다" in no_docker.stderr
    assert not box["args"].exists()
