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
    assert "workspace.n150.yaml" in err and "0123456789abcdef" in err and "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST" in err


def test_a_changed_workspace_file_is_refused(project, capsys):
    path = project / "config/dagster-shared/workspace.n150.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    code, _out, err = _validate(capsys)
    assert code == 1 and "sha256[:16]" in err


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


def test_the_check_sees_both_plane_services():
    # 하한: compose에서 digest env와 workspace bind를 함께 가진 서비스를 실제로 찾는다(daemon·webserver).
    compose = load_yaml_rejecting_duplicate_keys((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    carriers = [
        name for name, service in compose["services"].items()
        if "KOR_TRAVEL_DAGSTER_WORKSPACE_DIGEST" in (service.get("environment") or {})
    ]
    assert len(carriers) >= 2
    assert workspace_digest_violations(compose, ROOT, lambda name: "n150" if name == "KTDM_HOST" else "") == []
    wrong = workspace_digest_violations(compose, ROOT, lambda name: "n150" if name == "KTDM_HOST" else
                                        ("ffffffffffffffff" if name == "KTDM_DAGSTER_WORKSPACE_DIGEST" else ""))
    assert len(wrong) == 1  # 같은 (파일, digest)는 한 번만 말한다


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
    assert len(values) == 7 and set(values.values()) == {expected}
