"""외부 Transport core/admin current에서 public UI만 바꾸고 기존 schema/수집기를 보존한다."""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest
import test_service_release_candidate as paired

from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services.registry import (
    component_ui_candidate_for_target,
    load_targets_config,
)

scenario = paired.scenario
FRONTEND = "frontend"
OLD_BACKEND_SOURCE = "499822601abca3f050aa0cfe0e27fb446d22281d"
TRANSPORT_URL = "https://github.com/digitie/kor-travel-transport.git"


def test_registered_transport_frontend_uses_existing_external_core_group(monkeypatch):
    config = load_targets_config()
    monkeypatch.setattr(entry, "current_host", lambda config: "odroid")
    assert component_ui_candidate_for_target("transport", config) == (FRONTEND, TRANSPORT_URL)
    assert component._selection("transport", config) == ("core", FRONTEND, TRANSPORT_URL)
    groups = entry._declaration("transport", config)["groups"]
    assert set(groups) == {"core", "admin"}
    assert groups["core"]["runtime_services"] == ["backend", FRONTEND, "dagster-code-server"]
    assert groups["core"]["oneshot_services"] == ["migrate"]
    assert groups["admin"]["runtime_services"] == ["transport-admin-web", "transport-api-gateway"]
    assert groups["admin"]["oneshot_services"] == []


@pytest.fixture
def transport(scenario, monkeypatch):
    s = scenario
    config = load_targets_config()
    target = copy.deepcopy(config["targets"]["transport"])
    target["external_project"]["working_dir"] = str(s.source.parent)
    s.config = {
        "targets": {"transport": target},
        "containers": config["containers"],
        "service_releases": {"transport": config["service_releases"]["transport"]},
    }
    s.config["service_releases"] = copy.deepcopy(s.config["service_releases"])
    for group in s.config["service_releases"]["transport"]["groups"].values():
        group["env_file"] = str(s.source.parent / ".env.prod")
    for old, new in [(paired.UI, FRONTEND), ("api", "backend"), ("dagster", "dagster-code-server")]:
        s.records[new] = s.records.pop(old)
        s.records[new]["Config"]["Labels"]["com.docker.compose.service"] = new
    for name in ["transport-admin-web", "transport-api-gateway"]:
        s.records[name] = copy.deepcopy(s.records["backend"])
        s.records[name]["Id"] = "cid-" + name
        s.records[name]["Config"]["Labels"]["com.docker.compose.service"] = name
    for record in s.records.values():
        record["Config"]["Labels"]["com.docker.compose.project"] = "kor-travel-transport"
        record["Config"]["Env"] = ["PASSWORD=fixture$secret"]
    paired.git(s.source, "remote", "set-url", "origin", TRANSPORT_URL)
    migration = s.source / "backend/alembic/versions/0036.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("revision = '0036_parking_statistics_archive'\n")
    paired.git(s.source, "add", "backend")
    paired.git(s.source, "commit", "-qm", "candidate schema remains0036")
    s.sha = paired.git(s.source, "rev-parse", "HEAD")
    paired.git(s.source, "update-ref", "refs/remotes/origin/main", s.sha)
    for metadata in s.metadata.values():
        metadata["Config"]["Labels"]["org.opencontainers.image.source"] = TRANSPORT_URL
        metadata["Config"]["Labels"]["org.opencontainers.image.revision"] = OLD_BACKEND_SOURCE
    s.metadata[paired.NEW]["Config"]["Labels"]["org.opencontainers.image.revision"] = s.sha
    # 실제 배포의 newer schema는 candidate Git source와 별개다. SDK가 파일 동기화나
    # migration을 실행하면 이 기존 source/database 표식 보존 계약이 깨진다.
    s.live_schema = s.source.parent / "live-database-revision"
    s.live_schema.write_text("0042_reference_export_artifacts")
    s.live_backend = s.source.parent / "backend/app/main.py"
    s.live_backend.parent.mkdir(parents=True)
    s.live_backend.write_text("deployed499 backend")
    declaration = entry._declaration("transport", s.config)
    profile = entry._profile("transport", declaration, ["migrate=" + paired.OTHER])
    (s.root / "current/map").unlink()
    s.current = release.adopt_release(profile, root=s.root, client=s.client)
    # core Compose에는 admin 명령/환경 정의가 없어도 전체 images overlay가 남는다.
    s.resolved = {"services": {
        name: {"image": item["Image"]} for name, item in s.records.items()
    }}
    s.resolved["services"]["migrate"] = {"image": paired.OTHER}
    s.resolved["services"][FRONTEND]["depends_on"] = {"backend": {"condition": "service_healthy"}}
    monkeypatch.setattr(paired, "UI", FRONTEND)
    m = paired.prepare(s)
    monkeypatch.setattr(component, "manager_mutation_lock", m.manager_mutation_lock)
    for name in ["_baseline", "_receipt_images", "_paired_companions"]:
        monkeypatch.setattr(m, name, Mock(side_effect=AssertionError("not a paired deployment")))
    return s


def apply(s, *, execute=True):
    return component.apply_component_ui_candidate(
        "transport", image_id=paired.NEW, source_sha=s.sha, source_clone=s.source,
        root=s.root, config=s.config, client_factory=s.factory, execute=execute,
    )


@pytest.mark.parametrize("execute", [False, True])
def test_frontend_only_keeps_newer_backend_schema_and_both_current_groups(transport, execute):
    s = transport
    before = copy.deepcopy(s.records)
    schema = s.live_schema.read_bytes()
    backend = s.live_backend.read_bytes()
    result = apply(s, execute=execute)
    assert result["success"] is True
    assert result["result"] == ("healthy" if execute else "planned")
    assert s.live_schema.read_bytes() == schema == b"0042_reference_export_artifacts"
    assert s.live_backend.read_bytes() == backend == b"deployed499 backend"
    assert "0036_parking_statistics_archive" in (
        s.source / "backend/alembic/versions/0036.py").read_text()
    current = release.read_current_release("transport", root=s.root)
    assert current["groups"] == s.current["groups"]
    for name in set(current["images"]) - {FRONTEND}:
        assert current["images"][name] == s.current["images"][name]
    for name in set(s.records) - {FRONTEND}:
        assert s.records[name] == before[name]
    assert set(current["images"]) == {
        "backend", FRONTEND, "dagster-code-server", "migrate",
        "transport-admin-web", "transport-api-gateway",
    }
    ups = [item for item in s.calls if isinstance(item, tuple) and "up" in item[0]]
    if not execute:
        assert not ups
        assert current == s.current and s.records == before
        return
    assert current["images"][FRONTEND]["id"] == paired.NEW
    assert current["images"]["backend"]["source_sha"] == OLD_BACKEND_SOURCE
    assert current["images"][FRONTEND]["source_sha"] == s.sha
    assert current["source_sha"] is None
    assert len(ups) == 1
    command, kwargs = ups[0]
    assert command[command.index("up"):] == [
        "up", "-d", "--no-deps", "--no-build", "--pull", "never",
        "--force-recreate", "--wait", "--wait-timeout", "120", FRONTEND,
    ]
    frozen = json.loads(kwargs["input"])
    assert frozen["services"][FRONTEND]["image"] == paired.NEW
    for role in set(current["images"]) - {FRONTEND}:
        assert frozen["services"][role]["image"] == s.current["images"][role]["id"]
    assert "fixture$secret" not in json.dumps(result)


@pytest.mark.parametrize("role", ["backend", "dagster-code-server", "transport-admin-web",
                                  "transport-api-gateway", "db"])
def test_nonselected_external_runtime_change_prevents_publication(transport, role):
    s = transport
    s.on_mutation = lambda: s.records[role].update(RestartCount=1)
    result = apply(s)
    assert result["success"] is False and result["nonselected_unchanged"] is False
    assert release.read_current_release("transport", root=s.root) == s.current
