"""등록된 관리자 UI만 교체하며 버전 표식 외 환경·운영 source·DB를 보존한다."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import pytest
import test_service_release_candidate as paired
import test_service_release_transport_ui_candidate as public

from kor_travel_docker_manager import cli
from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services.c6c_deployment import ManagerMutationActiveError
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    component_ui_candidate_for_target,
    component_ui_release_environment_for_target,
    load_targets_config,
)

scenario = paired.scenario
transport = public.transport
ADMIN = "transport-admin-web"
REVISION_KEY = "TRANSPORT_ADMIN_RELEASE_SHA"
OLD_REVISION = "6bd594e606ef7d0326ec5742942e83997708946e"


@pytest.fixture
def admin(transport, monkeypatch):
    s = transport
    monkeypatch.setattr(paired, "UI", ADMIN)
    s.records[ADMIN]["Config"]["Env"] = [
        "TRANSPORT_UI_USERNAME=fixture-user",
        "TRANSPORT_UI_PASSWORD=fixture$password",
        "TRANSPORT_UI_SESSION_SECRET=fixture$$session",
        "TRANSPORT_ADMIN_WRITE_TOKEN=fixture-token",
        "NEXT_PUBLIC_VWORLD_API_KEY=fixture$map",
        "DATABASE_URL=fixture-database",
        REVISION_KEY + "=" + OLD_REVISION,
    ]
    # current metadata는 이미지·건강 계약이며 runtime 환경은 live inspect가 정본이다.
    s.metadata[paired.NEW]["Config"]["Env"] = [REVISION_KEY + "=" + s.sha]
    def recreate_environment():
        _, kwargs = next(item for item in reversed(s.calls)
                         if isinstance(item, tuple) and "up" in item[0])
        values = json.loads(kwargs["input"])["services"][ADMIN]["environment"]
        s.records[ADMIN]["Config"]["Env"] = [
            key + "=" + value.replace("$$", "$") for key, value in values.items()]
    s.recreate_environment = recreate_environment
    s.on_mutation = recreate_environment
    return s


def apply(s, *, execute=True, selector="admin"):
    return component.apply_component_ui_candidate(
        "transport", component=selector, image_id=paired.NEW, source_sha=s.sha,
        source_clone=s.source, root=s.root, config=s.config,
        client_factory=s.factory, execute=execute,
    )


def test_admin_name_is_bound_to_existing_owned_runtime_without_new_lifecycle_scope(monkeypatch):
    config = load_targets_config()
    monkeypatch.setattr(entry, "current_host", lambda config: "odroid")
    assert component._selection("transport", config, "admin") == (
        "admin", ADMIN, public.TRANSPORT_URL)
    assert component_ui_candidate_for_target("transport", config, component="admin") == (
        ADMIN, public.TRANSPORT_URL)
    assert component_ui_release_environment_for_target(
        "transport", config, component="admin") == REVISION_KEY
    assert component_ui_release_environment_for_target("transport", config) is None
    assert config["targets"]["transport"]["services"] == ["backend", "frontend"]
    assert config["targets"]["transport"]["containers"] == [
        "kor-travel-transport-backend", "kor-travel-transport-frontend"]
    assert "kor-travel-transport-admin-web" not in config["containers"]


@pytest.mark.parametrize("execute", [False, True])
def test_admin_only_changes_approved_revision_marker_and_preserves_auth_schema_and_public(admin, execute):
    s = admin
    before = copy.deepcopy(s.records)
    source = s.live_backend.read_bytes()
    schema = s.live_schema.read_bytes()
    result = apply(s, execute=execute)
    assert result["success"] is True
    current = release.read_current_release("transport", root=s.root)
    assert current["groups"] == s.current["groups"]
    assert s.live_backend.read_bytes() == source
    assert s.live_schema.read_bytes() == schema == b"0042_reference_export_artifacts"
    assert {k: v for k, v in s.records.items() if k != ADMIN} == {
        k: v for k, v in before.items() if k != ADMIN}
    if not execute:
        assert result["result"] == "planned"
        assert s.records == before and current == s.current
        return
    assert result["result"] == "healthy"
    assert result["selected_environment_unchanged"] is True
    before_env = dict(item.split("=", 1) for item in before[ADMIN]["Config"]["Env"])
    after_env = dict(item.split("=", 1) for item in s.records[ADMIN]["Config"]["Env"])
    assert after_env == {**before_env, REVISION_KEY: s.sha}
    assert current["images"][ADMIN]["id"] == paired.NEW
    for role in set(current["images"]) - {ADMIN}:
        assert current["images"][role] == s.current["images"][role]
    command, _ = next(item for item in s.calls if isinstance(item, tuple) and "up" in item[0])
    assert command[command.index("up"):] == [
        "up", "-d", "--no-deps", "--no-build", "--pull", "never",
        "--force-recreate", "--wait", "--wait-timeout", "120", ADMIN]
    for value in ["fixture$password", "fixture$$session", "fixture-token", "fixture$map", "fixture-database"]:
        assert value not in json.dumps(result)


@pytest.mark.parametrize("value", [None, "", "unknown", OLD_REVISION])
def test_candidate_image_revision_environment_must_match_approved_source_before_up(admin, value):
    s = admin
    s.metadata[paired.NEW]["Config"]["Env"] = [] if value is None else [REVISION_KEY + "=" + value]
    with pytest.raises(release.ServiceReleaseError, match="source revision"):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize("selector", ["unknown", "frontend", "", "backend", "admin/../core", 1])
def test_unregistered_selector_rejects_before_docker(admin, selector):
    with pytest.raises(release.ServiceReleaseError):
        apply(admin, selector=selector)
    assert "factory" not in admin.calls


@pytest.mark.parametrize("extra", [
    {}, {"runtime": "backend"}, {"runtime": "migrate"}, {"runtime": "foreign-web"},
    {"runtime": "frontend"}, {"runtime": ADMIN, "unknown": True},
    {"runtime": ADMIN, "release_env": "TRANSPORT_UI_PASSWORD"},
    {"runtime": ADMIN, "release_env": "TRANSPORT_UI_SESSION_SECRET"},
    {"runtime": ADMIN, "release_env": "NEXT_PUBLIC_VWORLD_API_KEY"},
    {"runtime": ADMIN, "release_env": "DATABASE_URL"},
    {"runtime": ADMIN, "release_env": True},
])
def test_named_selection_cannot_expand_to_api_oneshot_foreign_or_secret_settings(admin, extra):
    s = admin
    s.config["targets"]["transport"]["ui_candidate"]["components"]["admin"] = extra
    with pytest.raises(TargetsConfigError):
        component_ui_candidate_for_target("transport", s.config, component="admin")
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert "factory" not in s.calls


@pytest.mark.parametrize("role", ["backend", "frontend", "dagster-code-server",
                                  "transport-api-gateway", "db"])
def test_admin_nonselected_race_blocks_publication(admin, role):
    s = admin
    def raced():
        s.recreate_environment()
        s.records[role].update(RestartCount=1)
    s.on_mutation = raced
    result = apply(s)
    assert result["success"] is False and result["nonselected_unchanged"] is False
    assert release.read_current_release("transport", root=s.root) == s.current


@pytest.mark.parametrize("key", ["TRANSPORT_UI_PASSWORD", "TRANSPORT_UI_SESSION_SECRET",
                                "NEXT_PUBLIC_VWORLD_API_KEY", "DATABASE_URL", REVISION_KEY])
def test_admin_unapproved_environment_after_recreate_blocks_publication(admin, key):
    s = admin
    def raced():
        s.recreate_environment()
        values = dict(item.split("=", 1) for item in s.records[ADMIN]["Config"]["Env"])
        values[key] = "fixture-unapproved"
        s.records[ADMIN]["Config"]["Env"] = [k + "=" + v for k, v in values.items()]
    s.on_mutation = raced
    result = apply(s)
    assert result["success"] is False and result["selected_environment_unchanged"] is False
    assert release.read_current_release("transport", root=s.root) == s.current
    assert "fixture-unapproved" not in json.dumps(result)


def test_cli_selects_only_registered_component_name_not_arbitrary_role():
    args = cli.build_parser().parse_args([
        "service-release", "apply-component-ui", "transport", "--component", "admin",
        "--image-id", paired.NEW, "--source-sha", "d" * 40,
        "--source-clone", "/trusted/transport", "--dry-run",
    ])
    assert args.component == "admin" and args.dry_run


def test_admin_g_contention_never_opens_docker(admin, monkeypatch):
    @contextmanager
    def busy():
        raise ManagerMutationActiveError("busy")
        yield
    monkeypatch.setattr(component, "manager_mutation_lock", busy)
    with pytest.raises(ManagerMutationActiveError):
        apply(admin)
    assert "factory" not in admin.calls


def test_cli_dispatch_forwards_registered_admin_selector(admin, monkeypatch, capsys):
    s = admin
    monkeypatch.setattr(entry, "load_targets_config", lambda: s.config)
    captured = {}
    def planned(service, **options):
        captured.update(service=service, **options)
        return {"success": True, "result": "planned"}
    monkeypatch.setattr(component, "apply_component_ui_candidate", planned)
    args = cli.build_parser().parse_args([
        "service-release", "apply-component-ui", "transport", "--component", "admin",
        "--image-id", paired.NEW, "--source-sha", s.sha,
        "--source-clone", str(s.source), "--root", str(s.root), "--dry-run",
    ])
    assert entry._run(args) == 0
    assert captured["service"] == "transport" and captured["component"] == "admin"
    assert captured["execute"] is False
    assert json.loads(capsys.readouterr().out)["result"] == "planned"


def test_old_image_revision_cannot_change_ahead_of_candidate_replacement(admin, monkeypatch):
    s = admin
    m = paired.candidate_module()
    original = m._render
    def raced(*args, **kwargs):
        rendered = original(*args, **kwargs)
        values = dict(item.split("=", 1) for item in s.records[ADMIN]["Config"]["Env"])
        values[REVISION_KEY] = s.sha
        s.records[ADMIN]["Config"]["Env"] = [k + "=" + v for k, v in values.items()]
        return rendered
    monkeypatch.setattr(m, "_render", raced)
    with pytest.raises(release.ServiceReleaseError, match="환경"):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


@pytest.mark.parametrize("label,value", [
    ("org.opencontainers.image.revision", "f" * 40),
    ("org.opencontainers.image.source", "https://github.com/other/wrong.git"),
])
def test_admin_image_environment_cannot_override_oci_approval(admin, label, value):
    s = admin
    s.metadata[paired.NEW]["Config"]["Labels"][label] = value
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))
    assert release.read_current_release("transport", root=s.root) == s.current
