"""Transport core 적용 성공 뒤 best-effort warm(scripts/post-deploy-warm.sh) 계약."""

import argparse
import subprocess
import time

import pytest

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import persistent_dagster_state as persistent
from kor_travel_docker_manager.services import service_release_core_candidate as core
from kor_travel_docker_manager.services.registry import load_targets_config

SECRET = "fixture-warm-secret-value"


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _config():
    return {
        "targets": {"transport": {"external_project": {"project": "kor-travel-transport"}}},
        "containers": {
            "kor-travel-transport-backend": {
                "external_project": "kor-travel-transport",
                "compose_service": "backend",
                "expected_ports": ["12302:12302"],
            },
            "other-backend": {
                "external_project": "kor-travel-transport-fuel",
                "compose_service": "backend",
                "expected_ports": ["1:1"],
            },
        },
    }


@pytest.fixture
def clone(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    git(source, "init", "-q")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    monkeypatch.setattr(core, "_warm_secrets", lambda root: [("PASSWORD", SECRET)])

    def commit(body):
        (source / core.WARM_SCRIPT).write_text(body)
        git(source, "add", "-A")
        git(source, "commit", "-qm", "warm")
        return git(source, "rev-parse", "HEAD")

    return source, commit


def _warm(source, sha, **kwargs):
    return core.post_deploy_warm(source_clone=source, source_sha=sha, root=source.parent,
                                 config=_config(), **kwargs)


def test_real_config_derives_transport_project_and_api_port():
    config = load_targets_config()
    project = config["targets"]["transport"]["external_project"]["project"]
    # Transport 스크립트의 기본값(--project kor-travel-transport --api-port 12302)과 같은 좌표다.
    assert project == "kor-travel-transport"
    assert core._transport_backend_api_port(config, project) == 12302


def test_runs_approved_blob_with_local_docker_and_records_warnings(clone):
    source, commit = clone
    sha = commit(
        'echo "argv=$*"\n'
        'echo "docker=$DOCKER_HOST context=${DOCKER_CONTEXT:-none} token=${TRANSPORT_TOKEN:-none}"\n'
        'echo "post-deploy-warm: reference prepare (1s): reused"\n'
        f'echo "post-deploy-warm: WARN export smoke: leaked {SECRET}" >&2\n'
        'echo "post-deploy-warm: WARN backend is not running" >&2\n'
        "exit 0\n"
    )
    # 적용 뒤 작업 트리가 바뀌어도 승인 SHA의 본문만 실행한다.
    (source / core.WARM_SCRIPT).write_text("echo tampered; exit 0\n")
    result = _warm(source, sha)
    assert result["status"] == "completed" and result["exit_code"] == 0
    assert "tampered" not in result["stdout"]
    assert "argv=--project kor-travel-transport --api-port 12302" in result["stdout"]
    assert f"docker={persistent.LOCAL_DOCKER_HOST} context=none token=none" in result["stdout"]
    assert result["warning_count"] == 2 and result["clean"] is False
    assert result["warnings"][1] == "post-deploy-warm: WARN backend is not running"
    assert SECRET not in repr(result) and result["output_redacted"] is True


def test_parent_docker_and_secret_environment_is_not_inherited(clone, monkeypatch):
    source, commit = clone
    monkeypatch.setenv("DOCKER_HOST", "tcp://elsewhere:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("TRANSPORT_TOKEN", SECRET)
    sha = commit('echo "docker=$DOCKER_HOST context=${DOCKER_CONTEXT:-none} token=${TRANSPORT_TOKEN:-none}"\n')
    result = _warm(source, sha)
    assert f"docker={persistent.LOCAL_DOCKER_HOST} context=none token=none" in result["stdout"]
    assert result["clean"] is True


@pytest.mark.parametrize(("code", "status"), [(2, "invalid_arguments"), (7, "failed")])
def test_nonzero_exit_is_recorded_not_raised(clone, code, status):
    source, commit = clone
    sha = commit(f"echo 'post-deploy-warm: invalid arguments' >&2\nexit {code}\n")
    result = _warm(source, sha)
    assert result["status"] == status and result["exit_code"] == code and result["clean"] is False


def test_timeout_kills_the_whole_process_group(clone, tmp_path):
    source, commit = clone
    marker = tmp_path / "child-survived"
    sha = commit(f"(sleep 3; touch {marker}) &\nsleep 30\n")
    started = time.monotonic()
    result = _warm(source, sha, timeout=1)
    assert result["status"] == "timeout" and time.monotonic() - started < 10
    time.sleep(3.5)
    assert not marker.exists()


def test_missing_script_at_approved_sha_is_unavailable(clone):
    source, commit = clone
    (source / "README").write_text("old transport")
    git(source, "add", "README")
    git(source, "commit", "-qm", "old")
    sha = git(source, "rev-parse", "HEAD")
    result = _warm(source, sha)
    assert result["status"] == "unavailable" and result["warnings"] == []


def test_unreadable_env_withholds_output_but_keeps_warning_count(clone, monkeypatch):
    source, commit = clone
    monkeypatch.setattr(core, "_warm_secrets", lambda root: None)
    sha = commit(f"echo 'post-deploy-warm: WARN {SECRET}' >&2\n")
    result = _warm(source, sha)
    assert result["warning_count"] == 1 and result["warnings"] == []
    assert result["output_redacted"] is False and result["clean"] is False
    assert SECRET not in repr(result)


def test_bad_port_declaration_is_error_not_exception(clone):
    source, commit = clone
    sha = commit("exit 0\n")
    config = _config()
    config["containers"]["kor-travel-transport-backend"]["expected_ports"] = ["12302:12302", "1:1"]
    result = core.post_deploy_warm(source_clone=source, source_sha=sha, root=source.parent, config=config)
    assert result["status"] == "error"


def _args(*extra):
    parser = argparse.ArgumentParser()
    entry.add_service_release_parser(parser.add_subparsers())
    return parser.parse_args([
        "service-release", "apply-core-candidate", "transport",
        "--source-clone", "/approved/source", "--source-sha", "a" * 40,
        "--build-receipt", "/approved/build.json", "--build-sha256", "b" * 64,
        "--receipt", "/approved/runtime.json", "--sha256", "c" * 64, *extra,
    ])


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(entry, "load_targets_config", _config)
    monkeypatch.setattr(entry, "_declaration", lambda service, config: {})
    state = {"apply": {"success": True, "result": "healthy"}, "warm": []}
    monkeypatch.setattr(core, "apply_transport_core_candidate", lambda *a, **k: dict(state["apply"]))

    def warm(**kwargs):
        state["warm"].append(kwargs)
        return {"status": "failed", "exit_code": 9,
                "warnings": ["post-deploy-warm: WARN export smoke did not complete (exit 9)"]}

    monkeypatch.setattr(core, "post_deploy_warm", warm)
    return state


def test_cli_warms_after_successful_confirm_without_changing_exit(cli, capsys):
    assert entry._run(_args("--confirm")) == 0
    out = capsys.readouterr()
    assert '"post_deploy_warm"' in out.out and '"exit_code": 9' in out.out
    assert "WARN export smoke did not complete" in out.err
    assert cli["warm"][0]["source_sha"] == "a" * 40
    assert str(cli["warm"][0]["source_clone"]) == "/approved/source"


@pytest.mark.parametrize("apply", [
    {"success": False, "result": "failed", "phase": "start"},
    {"success": True, "result": "planned"},
])
def test_cli_does_not_warm_failed_or_planned(cli, capsys, apply):
    cli["apply"] = apply
    extra = "--confirm" if apply["result"] == "failed" else "--dry-run"
    entry._run(_args(extra))
    assert cli["warm"] == [] and "post_deploy_warm" not in capsys.readouterr().out


def test_cli_operator_can_skip_warm(cli, capsys):
    assert entry._run(_args("--confirm", "--skip-post-deploy-warm")) == 0
    assert cli["warm"] == []
    assert '"reason": "operator"' in capsys.readouterr().out
