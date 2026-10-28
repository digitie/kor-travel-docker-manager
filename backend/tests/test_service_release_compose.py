"""현재 release 재생성의 범위·고정 입력·환경 격리·오류 가림 회귀."""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager.services import service_release_compose as compose
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
C = "sha256:" + "c" * 64


@pytest.fixture
def scenario(monkeypatch):
    group = {"project": "shared", "working_dir": "/srv/apps", "env_file": "/srv/apps/.env",
             "runtime_services": ["api", "web"], "oneshot_images": {"migrate": B}}
    manifests = {"weather": {"service": "weather", "groups": {"main": group},
                            "images": {"api": {"id": A}, "web": {"id": A}, "migrate": {"id": B}}}}
    plan = {"service": "weather", "group": "main", "project": "shared",
            "runtime_services": ["api", "web"],
            "argv": ["docker", "compose", "--project-name", "shared",
                     "--project-directory", "/srv/apps", "--env-file", "/srv/apps/.env",
                     "-f", "/srv/apps/base.yml", "-f", "/releases/weather/images.yml"]}
    resolved = {"name": "shared", "services": {
        "api": {"image": "api:tag", "environment": {"APP_PASSWORD": "resolved$private"},
                "command": ["sh", "-c", "echo $$UNCHANGED"]},
        "web": {"image": A}, "migrate": {"image": B},
    }}
    client = SimpleNamespace(images=SimpleNamespace(get=lambda ref:
        SimpleNamespace(attrs={"Id": A if ref == "api:tag" else ref})))
    verified = Mock(return_value={"result": "healthy", "images": {}})
    monkeypatch.setattr(compose.service_release, "verify_release_manifest", verified, raising=False)
    monkeypatch.setattr(compose, "load_secret_environment", lambda path:
                        [("APP_TOKEN", "app-private-token")])
    monkeypatch.setattr(compose, "_mutation_lock_fd", lambda: 123)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(resolved), "")
        return subprocess.CompletedProcess(argv, 0, "SECRET-stdout-must-not-return", "")

    monkeypatch.setattr(compose.subprocess, "run", run)
    return SimpleNamespace(plan=plan, manifests=manifests, resolved=resolved,
                           client=client, verified=verified, calls=calls)


def recreate(scenario, **kwargs):
    return compose.recreate_current(scenario.plan, scenario.manifests,
                                    client=scenario.client, **kwargs)


def test_dry_run_only_resolves_and_returns_safe_image_ids(scenario):
    result = recreate(scenario)
    assert result["success"] is True
    assert result["result"] == "resolved"
    assert result["images"] == {"api": A, "web": A, "migrate": B}
    assert len(scenario.calls) == 1
    assert scenario.calls[0][0][-3:] == ["config", "--format", "json"]
    assert scenario.calls[0][1]["timeout"] == 30
    assert "resolved$private" not in json.dumps(result)
    assert "environment" not in result
    scenario.verified.assert_called_once_with(scenario.manifests["weather"], client=scenario.client)


def test_recreate_scopes_runtime_without_oneshot_build_pull_or_dependencies(scenario):
    result = recreate(scenario, execute=True, wait_timeout=77)
    argv, kwargs = scenario.calls[1]
    assert argv == ["docker", "compose", "--project-name", "shared",
                    "--project-directory", "/srv/apps", "--env-file", "/dev/null", "-f", "-",
                    "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                    "--wait", "--wait-timeout", "77", "api", "web"]
    assert "migrate" not in argv
    assert "timeout" not in kwargs
    assert result["result"] == "healthy"
    assert result["success"] is True
    assert scenario.verified.call_count == 2
    assert "SECRET-stdout" not in json.dumps(result)


def test_frozen_stdin_pins_resolved_ids_and_preserves_dollar_values(scenario):
    original = deepcopy(scenario.resolved)
    recreate(scenario, execute=True)
    frozen = json.loads(scenario.calls[1][1]["input"])
    assert frozen["services"]["api"]["image"] == A
    assert frozen["services"]["api"]["environment"]["APP_PASSWORD"] == "resolved$$private"
    assert frozen["services"]["api"]["command"] == ["sh", "-c", "echo $$UNCHANGED"]
    assert scenario.resolved == original


def test_child_environment_excludes_manager_values_and_preserves_docker_transport(scenario, monkeypatch):
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("DOCKER_HOST", "unix:///socket")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", "/manager/.env")
    monkeypatch.setenv("APP_PASSWORD", "must-not-inherit")
    monkeypatch.setenv("KTDM_HOST", "odroid")
    recreate(scenario, execute=True)
    for _, kwargs in scenario.calls:
        env = kwargs["env"]
        assert env["PATH"] == "/bin"
        assert env["DOCKER_HOST"] == "unix:///socket"
        assert "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE" not in env
        assert "APP_PASSWORD" not in env
        assert "KTDM_HOST" not in env


@pytest.mark.parametrize("execute", [False, True])
def test_unhealthy_current_cannot_reach_compose(scenario, execute):
    scenario.verified.return_value = {"result": "drift", "issues": ["api stopped"]}
    with pytest.raises(ServiceReleaseError, match="healthy"):
        recreate(scenario, execute=execute)
    assert scenario.calls == []


@pytest.mark.parametrize("broken", ["wrong-id", "missing-role", "bad-json", "wrong-root"])
def test_bad_resolved_config_cannot_reach_mutation(scenario, monkeypatch, broken):
    if broken == "wrong-id":
        scenario.resolved["services"]["api"]["image"] = C
    elif broken == "missing-role":
        del scenario.resolved["services"]["migrate"]
    else:
        def run(argv, **kwargs):
            scenario.calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "invalid-json" if broken == "bad-json" else "[]", "")
        monkeypatch.setattr(compose.subprocess, "run", run)
    with pytest.raises(ServiceReleaseError):
        recreate(scenario, execute=True)
    assert len(scenario.calls) == 1
    assert "config" in scenario.calls[0][0]


def test_all_shared_manifest_images_and_health_are_required(scenario):
    scenario.manifests["geo"] = {
        "service": "geo", "groups": {"main": {
            "project": "shared", "working_dir": "/srv/apps", "env_file": None,
            "runtime_services": ["geo-api"], "oneshot_images": {}}},
        "images": {"geo-api": {"id": C}}}
    scenario.resolved["services"]["geo-api"] = {"image": C}
    result = recreate(scenario, execute=True)
    assert result["images"]["geo-api"] == C
    assert scenario.verified.call_count == 4
    assert scenario.calls[1][0][-2:] == ["api", "web"]
    del scenario.resolved["services"]["geo-api"]
    with pytest.raises(ServiceReleaseError):
        recreate(scenario, execute=True)
    assert len(scenario.calls) == 3


def test_process_failure_retains_cause_after_redaction_and_verifies_again(scenario, monkeypatch):
    def run(argv, **kwargs):
        scenario.calls.append((argv, kwargs))
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(scenario.resolved), "")
        return subprocess.CompletedProcess(argv, 17, "private stdout", "bind failed: app-private-token resolved$private " + "x" * 5000)
    monkeypatch.setattr(compose.subprocess, "run", run)
    result = recreate(scenario, execute=True)
    assert result["result"] == "failed"
    assert result["returncode"] == 17
    assert result["stderr"].startswith("bind failed: <redacted> <redacted>")
    assert len(result["stderr"]) == 4000
    assert "private stdout" not in json.dumps(result)
    assert scenario.verified.call_count == 2
    assert "post_verification" in result


def test_config_failure_redacts_app_environment_before_return(scenario, monkeypatch):
    monkeypatch.setattr(compose.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 2, "SECRET-config", "missing required app-private-token"))
    result = recreate(scenario, execute=True)
    assert result["returncode"] == 2
    assert result["stderr"] == "missing required <redacted>"
    assert "SECRET-config" not in json.dumps(result)
    assert scenario.verified.call_count == 1


def test_unreadable_secret_source_withholds_raw_failure(scenario, monkeypatch):
    monkeypatch.setattr(compose, "load_secret_environment", Mock(side_effect=OSError("private path")))
    monkeypatch.setattr(compose.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 2, "SECRET-config", "SECRET-error"))
    result = recreate(scenario, execute=True)
    assert "SECRET" not in json.dumps(result)
    assert "환경파일" in result["stderr"]


def test_successful_process_with_post_drift_is_not_healthy(scenario):
    scenario.verified.side_effect = [{"result": "healthy"}, {"result": "drift", "issues": ["wrong ID"]}]
    result = recreate(scenario, execute=True)
    assert result["success"] is False
    assert result["result"] == "drift"


@pytest.mark.parametrize("timeout", [0, -1, True, "120"])
def test_wait_timeout_must_be_positive_integer_before_any_lookup(scenario, timeout):
    with pytest.raises(ServiceReleaseError, match="양의 정수"):
        recreate(scenario, execute=True, wait_timeout=timeout)
    assert scenario.calls == []
    scenario.verified.assert_not_called()


def test_config_timeout_preserves_redacted_cause_without_mutation(scenario, monkeypatch):
    def run(argv, **kwargs):
        scenario.calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(argv, 30, output="SECRET-stdout",
                                        stderr="config waiting app-private-token")
    monkeypatch.setattr(compose.subprocess, "run", run)
    result = recreate(scenario, execute=True)
    assert result["result"] == "failed"
    assert result["error_type"] == "TimeoutExpired"
    assert result["stderr"] == "config waiting <redacted>"
    assert len(scenario.calls) == 1
    assert "SECRET-stdout" not in json.dumps(result)


def test_mutation_launch_failure_preserves_cause_and_checks_fixed_manifests(scenario, monkeypatch):
    def run(argv, **kwargs):
        scenario.calls.append((argv, kwargs))
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(scenario.resolved), "")
        raise OSError("cannot launch: app-private-token")
    monkeypatch.setattr(compose.subprocess, "run", run)
    result = recreate(scenario, execute=True)
    assert result["result"] == "failed"
    assert result["error_type"] == "OSError"
    assert result["stderr"] == "cannot launch: <redacted>"
    assert scenario.verified.call_count == 2


def test_any_shared_manifest_drift_prevents_all_compose_calls(scenario):
    scenario.manifests["geo"] = {
        "service": "geo", "groups": {"main": {
            "project": "shared", "working_dir": "/srv/apps", "env_file": None,
            "runtime_services": ["geo-api"], "oneshot_images": {}}},
        "images": {"geo-api": {"id": C}}}
    scenario.verified.side_effect = [{"result": "healthy"}, {"result": "drift"}]
    with pytest.raises(ServiceReleaseError, match="healthy"):
        recreate(scenario, execute=True)
    assert scenario.calls == []


def test_preflight_keeps_service_and_runtime_failure_reason(scenario, monkeypatch):
    monkeypatch.setattr(compose.service_release, "verify_release_manifest",
                        lambda manifest, **kwargs: {"result": "drift", "issues": ["api: running/healthy 조건 실패"]})
    with pytest.raises(ServiceReleaseError, match="weather: api: running/healthy 조건 실패"):
        compose.recreate_current(scenario.plan, scenario.manifests, client=scenario.client, execute=True)
    assert scenario.calls == []
