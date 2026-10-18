"""T-MGR-02 잔여: `targets validate`가 설치된 workspace 파일 sha256[:16] == digest env를 효과로 본다 + instance digest anchor.

공용 plane healthcheck가 같은 대조를 하므로, 어긋나면 설치·재생성 뒤 plane이 unhealthy가 된다. validate(설치기 flip 전
검사 포함)에서 먼저 멈춘다.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from kor_travel_docker_manager.services import registry
from kor_travel_docker_manager.services.workspace_digest import workspace_digest_violations
from kor_travel_docker_manager.services.yaml_strict import load_yaml_rejecting_duplicate_keys

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """실제 registry·compose·공용 Dagster 설정을 가진 개발 checkout 모양(비운영 — 주소 검사 없음)."""
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config/docker-targets.yml", tmp_path / "config/docker-targets.yml")
    shutil.copytree(ROOT / "config/dagster-shared", tmp_path / "config/dagster-shared")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    monkeypatch.setenv(registry.PROJECT_ROOT_ENV, str(tmp_path))
    for name in (registry.TARGETS_FILE_ENV, registry.HOST_ENV, "KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE",
                 "KOR_TRAVEL_DOCKER_MANAGER_COMPOSE_FILE"):
        monkeypatch.delenv(name, raising=False)
    registry.load_targets_config.cache_clear()
    yield tmp_path
    registry.load_targets_config.cache_clear()


def _validate(capsys) -> tuple[int, str, str]:
    from kor_travel_docker_manager.cli import main

    try:
        code = main(["targets", "validate", "--json"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def test_the_committed_default_host_workspace_matches_its_digest(project, capsys):
    code, out, err = _validate(capsys)
    assert code == 0, err
    assert json.loads(out)["status"] == "ok"


def test_a_wrong_digest_override_is_refused(project, capsys):
    (project / ".env").write_text("KTDM_DAGSTER_WORKSPACE_DIGEST=0123456789abcdef\n", encoding="utf-8")
    code, out, err = _validate(capsys)
    assert code == 1 and out == ""
    assert "differs from frozen plane digests" in err
    assert "workspace.n150.yaml" in err and "0123456789abcdef" in err and "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST" in err


def test_a_changed_workspace_file_is_refused(project, capsys):
    path = project / "config/dagster-shared/workspace.n150.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    code, _out, err = _validate(capsys)
    assert code == 1 and "sha256[:16]" in err and "differs from frozen plane digests" in err


def test_another_host_needs_its_own_digest_in_root_env(project, monkeypatch, capsys):
    """다른 호스트(odroid)는 자기 workspace 파일을 마운트한다 — 기본 digest(default 호스트 파일)로는 거부, 그 파일의 digest를
    root `.env`에 두면 통과(`.env.example`의 계산식)."""
    other = next(name for name in registry._host_specs(registry.load_targets_config()) if name != "n150")
    monkeypatch.setenv(registry.HOST_ENV, other)
    code, _out, err = _validate(capsys)
    assert code == 1 and f"workspace.{other}.yaml" in err
    digest = _digest(project / f"config/dagster-shared/workspace.{other}.yaml")
    (project / ".env").write_text(f"KTDM_DAGSTER_WORKSPACE_DIGEST={digest}\n", encoding="utf-8")
    code, _out, err = _validate(capsys)
    assert code == 0, err


def test_a_missing_workspace_file_is_refused(project, capsys):
    (project / "config/dagster-shared/workspace.n150.yaml").unlink()
    code, _out, err = _validate(capsys)
    assert code == 1 and "is missing" in err


def test_the_process_env_wins_even_when_empty(project, monkeypatch, capsys):
    """compose와 같은 우선순위 — 프로세스 env에 **있으면**(빈 값이어도) `.env`를 보지 않고, 빈 값은 `:-` 기본값을 고른다."""
    (project / ".env").write_text("KTDM_DAGSTER_WORKSPACE_DIGEST=0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setenv("KTDM_DAGSTER_WORKSPACE_DIGEST", "")
    code, _out, err = _validate(capsys)
    assert code == 0, err


def test_the_env_file_path_follows_the_manager_env_file_override(project, monkeypatch, capsys, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("envfile") / "custom.env"
    elsewhere.write_text("KTDM_DAGSTER_WORKSPACE_DIGEST=0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setenv("KOR_TRAVEL_DOCKER_MANAGER_ENV_FILE", str(elsewhere))
    code, _out, err = _validate(capsys)
    assert code == 1 and "0123456789abcdef" in err


def _documents() -> tuple[dict, dict]:
    compose = load_yaml_rejecting_duplicate_keys((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    targets = load_yaml_rejecting_duplicate_keys((ROOT / "config/docker-targets.yml").read_text(encoding="utf-8"))
    return compose, targets


def _plain(name: str) -> str | None:
    return None


def test_the_real_plane_passes(monkeypatch):
    monkeypatch.delenv(registry.HOST_ENV, raising=False)
    compose, targets = _documents()
    assert workspace_digest_violations(compose, targets, ROOT, _plain) == []


def test_a_renamed_workspace_bind_is_a_violation_not_a_silent_pass(monkeypatch):
    """리뷰 1번: bind 이름이 바뀌어 plane을 못 찾으면 대상 0개로 조용히 통과하지 않는다."""
    monkeypatch.delenv(registry.HOST_ENV, raising=False)
    compose, targets = _documents()
    for service in compose["services"].values():
        service["volumes"] = [
            str(volume).replace("/config/dagster-shared/workspace", "/config/dagster-shared/renamed")
            if isinstance(volume, str) else volume
            for volume in service.get("volumes") or []
        ]
    violations = workspace_digest_violations(compose, targets, ROOT, _plain)
    assert len(violations) == 1 and "shared" in violations[0]


def test_a_renamed_digest_env_is_a_violation(monkeypatch):
    monkeypatch.delenv(registry.HOST_ENV, raising=False)
    compose, targets = _documents()
    for service in compose["services"].values():
        environment = service.get("environment")
        if isinstance(environment, dict) and "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST" in environment:
            environment["KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST_RENAMED"] = environment.pop("KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST")
    violations = workspace_digest_violations(compose, targets, ROOT, _plain)
    assert len(violations) == 1 and "has no KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST" in violations[0]


def test_a_second_workspace_bind_on_a_plane_service_is_a_violation(monkeypatch):
    monkeypatch.delenv(registry.HOST_ENV, raising=False)
    compose, targets = _documents()
    for service in compose["services"].values():
        volumes = service.get("volumes") or []
        if any("config/dagster-shared/workspace." in str(volume) for volume in volumes) and not service.get("profiles"):
            volumes.append("./config/dagster-shared/workspace.odroid.yaml:/tmp/second.yaml:ro")
    violations = workspace_digest_violations(compose, targets, ROOT, _plain)
    assert len(violations) == 1 and "exactly one workspace" in violations[0]


# --- instance digest: 리터럴 한 자리(anchor) ----------------------------------------------------------------


def test_the_instance_digest_has_one_literal_and_matches_the_file():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    expected = _digest(ROOT / "config/dagster-shared/dagster.yaml")
    assert text.count(expected) == 1, "instance digest 리터럴은 anchor 한 자리여야 한다"
    assert re.search(rf'^\s+KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST: &dagster-instance-digest "{expected}"$', text, re.M)
    compose = load_yaml_rejecting_duplicate_keys(text)
    values = {
        name: service["environment"]["KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST"]
        for name, service in compose["services"].items()
        if "KOR_TRAVEL_DAGSTER_INSTANCE_DIGEST" in (service.get("environment") or {})
    }
    assert len(values) >= 1 and set(values.values()) == {expected}
