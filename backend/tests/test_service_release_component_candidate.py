"""기존 혼합 runtime 그룹에서 UI 하나만 바꾸고 nonpaired current를 발행한다."""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest
import test_service_release_candidate as paired

from kor_travel_docker_manager import cli
from kor_travel_docker_manager.services import service_release as release
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services.registry import (
    TargetsConfigError,
    component_ui_candidate_for_target,
    load_targets_config,
)

# 동일 fake Docker/승인 Git fixture를 공유하고 제품 test expectation은 수정하지 않는다.
scenario = paired.scenario


@pytest.fixture(params=[("geo", "geocoder-ui", "kor-travel-geo-ui"),
                        ("weather", "weather-web", "kor-travel-weather-web")])
def component_scenario(scenario, request, monkeypatch):
    s = scenario
    service, role, runtime = request.param
    s.service, s.runtime = service, runtime
    s.url = f"https://github.com/digitie/kor-travel-{service}.git"
    record = s.records.pop(paired.UI)
    record["Config"]["Labels"]["com.docker.compose.service"] = runtime
    s.records[runtime] = record
    record["Config"]["Env"] = ["PASSWORD=fixture$secret"]
    s.resolved["services"][runtime] = s.resolved["services"].pop(paired.UI)
    for metadata in s.metadata.values():
        metadata["Config"]["Labels"]["org.opencontainers.image.source"] = s.url
    paired.git(s.source, "remote", "set-url", "origin", s.url)
    old_group = s.profile["groups"]["ui"]
    group = {**old_group, "runtime_services": ["api", runtime, "dagster"]}
    if service == "weather":
        group["oneshot_images"] = {"migrate": paired.OTHER}
        s.resolved["services"]["migrate"] = {"image": paired.OTHER}
        s.resolved["services"][runtime]["depends_on"] = {"migrate": {"condition": "service_completed_successfully"}}
    s.profile = {"service": service, "groups": {"main": group}}
    s.config = {"targets": {service: {"host": "test", "containers": [runtime],
                                   "ui_candidate": {"role": role, "source_url": s.url}}},
                "containers": {runtime: {"compose_service": runtime, "role": role}},
                "service_releases": {service: {"groups": {"main": {
                    **{key: value for key, value in group.items() if key != "oneshot_images"},
                    "oneshot_services": list(group["oneshot_images"]),
                }}}}}
    (s.root / "current" / "map").unlink()
    s.current = release.adopt_release(s.profile, root=s.root, client=s.client)
    monkeypatch.setattr(paired, "UI", runtime)
    m = paired.prepare(s)
    monkeypatch.setattr(component, "manager_mutation_lock", m.manager_mutation_lock)
    # Geo/Weather의 current를 Map/Pinvi pair receipt라고 꾸며 사용하지 않는다.
    for name in ["_baseline", "_receipt_images", "_paired_companions"]:
        monkeypatch.setattr(m, name, Mock(side_effect=AssertionError("paired baseline must not be read")))
    return s


def apply(s, **overrides):
    values = {"service": s.service, "root": s.root, "source_clone": s.source,
              "source_sha": s.sha, "image_id": paired.NEW, "config": s.config,
              "client_factory": s.factory, "execute": True}
    values.update(overrides)
    return component.apply_component_ui_candidate(**values)


def test_existing_mixed_group_replaces_only_registered_ui(component_scenario):
    s = component_scenario
    before = copy.deepcopy(s.records)
    result = apply(s)
    assert result["success"] is True and result["result"] == "healthy"
    current = release.read_current_release(s.service, root=s.root)
    assert current["groups"] == s.current["groups"]  # 새 그룹/중복 소유권을 만들지 않는다.
    assert current["previous"] == s.current["release_directory"]
    assert current["images"][s.runtime]["id"] == paired.NEW
    assert current["images"][s.runtime]["source_sha"] == s.sha
    for name in set(current["images"]) - {s.runtime}:
        assert current["images"][name] == s.current["images"][name]
    assert {k: v for k, v in s.records.items() if k != s.runtime} == {
        k: v for k, v in before.items() if k != s.runtime}
    command, kwargs = next(item for item in s.calls if isinstance(item, tuple) and "up" in item[0])
    assert command[-1] == s.runtime
    assert command[command.index("up"):] == [
        "up", "-d", "--no-deps", "--no-build", "--pull", "never",
        "--force-recreate", "--wait", "--wait-timeout", "120", s.runtime]
    frozen = json.loads(kwargs["input"])
    assert frozen["services"][s.runtime]["image"] == paired.NEW
    assert frozen["services"]["api"]["image"] == paired.OTHER
    assert "fixture$secret" not in json.dumps(result)
    assert "baseline_unchanged" not in result  # 별도 paired 기준선 gate를 주장하지 않는다.
    assert s.client.close.call_count == 1


def test_dry_run_preserves_current_runtime_and_oneshot(component_scenario):
    s = component_scenario
    records = copy.deepcopy(s.records)
    result = apply(s, execute=False)
    assert result["result"] == "planned"
    assert s.records == records
    assert release.read_current_release(s.service, root=s.root) == s.current
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


@pytest.mark.parametrize("change", ["api-role", "missing-owner", "duplicate-role", "unknown-field",
                                   "foreign-origin", "oneshot-only", "root-required"])
def test_bad_selection_rejects_before_docker(component_scenario, change, monkeypatch):
    s = component_scenario
    target = s.config["targets"][s.service]
    if change == "api-role":
        target["ui_candidate"]["role"] = "geocoder-api"
    elif change == "missing-owner":
        target["containers"] = []
    elif change == "duplicate-role":
        target["containers"].append("duplicate")
        s.config["containers"]["duplicate"] = dict(s.config["containers"][s.runtime])
    elif change == "unknown-field":
        target["ui_candidate"]["runtime"] = "api"
    elif change == "foreign-origin":
        paired.git(s.source, "remote", "set-url", "origin", "https://github.com/other/wrong.git")
    elif change == "oneshot-only":
        s.config["service_releases"][s.service]["groups"]["main"]["runtime_services"].remove(s.runtime)
        s.config["service_releases"][s.service]["groups"]["main"]["oneshot_services"].append(s.runtime)
    elif change == "root-required":
        monkeypatch.setattr(component.os, "geteuid", lambda: 123)
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert "factory" not in s.calls
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


@pytest.mark.parametrize("overrides", [{"image_id": "latest"}, {"source_sha": "main"},
                                      {"wait_timeout": 0}, {"wait_timeout": True},
                                      {"execute": "yes"}])
def test_invalid_input_rejects_before_docker(component_scenario, overrides):
    s = component_scenario
    with pytest.raises(release.ServiceReleaseError):
        apply(s, **overrides)
    assert "factory" not in s.calls


def test_nonselected_restart_prevents_current_publication(component_scenario):
    s = component_scenario
    s.on_mutation = lambda: s.records["api"].update(RestartCount=1)
    result = apply(s)
    assert result["success"] is False
    assert result["nonselected_unchanged"] is False
    assert release.read_current_release(s.service, root=s.root) == s.current


def test_unhealthy_ui_prevents_publication(component_scenario):
    s = component_scenario
    s.on_mutation = lambda: s.records[s.runtime]["State"]["Health"].update(Status="unhealthy")
    result = apply(s)
    assert result["success"] is False
    assert release.read_current_release(s.service, root=s.root) == s.current


def test_wrong_candidate_source_prevents_mutation(component_scenario):
    s = component_scenario
    s.metadata[paired.NEW]["Config"]["Labels"]["org.opencontainers.image.source"] = "https://github.com/other/wrong.git"
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


@pytest.mark.parametrize("url", ["http://github.com/example/app.git", "https://user:pass@github.com/example/app.git",
                                "https://github.com:bad/example/app.git", "https://github.com/example/app.git?q=bad",
                                "https://[broken", 1, None])
def test_registry_rejects_invalid_source_urls(component_scenario, url):
    s = component_scenario
    s.config["targets"][s.service]["ui_candidate"]["source_url"] = url
    with pytest.raises(TargetsConfigError):
        component_ui_candidate_for_target(s.service, s.config)


def test_catalog_derives_ui_from_existing_registered_target_without_new_group(monkeypatch):
    from kor_travel_docker_manager import service_release_cli as entry
    config = load_targets_config()
    monkeypatch.setattr(entry, "current_host", lambda config: "odroid")
    for service in ["geo", "weather"]:
        runtime, url = component_ui_candidate_for_target(service, config)
        group, selected, source = component._selection(service, config)
        assert selected == runtime and source == url and group == "main"
        assert set(config["service_releases"][service]["groups"]) == {"main"}
        assert len(config["service_releases"][service]["groups"]["main"]["runtime_services"]) == 3


def test_cli_requires_explicit_approval_and_does_not_accept_arbitrary_role():
    parser = cli.build_parser()
    base = ["service-release", "apply-component-ui", "geo", "--image-id", paired.NEW,
            "--source-sha", "d" * 40, "--source-clone", "/trusted/geo"]
    with pytest.raises(SystemExit):
        parser.parse_args(base)
    args = parser.parse_args([*base, "--dry-run"])
    assert args.release_action == "apply-component-ui" and args.dry_run
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--confirm", "--role", "geocoder-api"])


def test_missing_current_rejects_before_docker(component_scenario):
    s = component_scenario
    (s.root / "current" / s.service).unlink()
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert "factory" not in s.calls


def test_unhealthy_nonselected_api_rejects_before_mutation(component_scenario):
    s = component_scenario
    s.records["api"]["State"]["Health"]["Status"] = "unhealthy"
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


def test_wrong_revision_rejects_before_mutation(component_scenario):
    s = component_scenario
    s.metadata[paired.NEW]["Config"]["Labels"]["org.opencontainers.image.revision"] = "f" * 40
    with pytest.raises(release.ServiceReleaseError):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


@pytest.mark.parametrize("service", ["map", "pinvi"])
def test_component_route_cannot_bypass_existing_paired_authority(component_scenario, service):
    s = component_scenario
    with pytest.raises(release.ServiceReleaseError, match="paired"):
        apply(s, service=service)
    assert "factory" not in s.calls


def materialized_environment(s):
    _, kwargs = next(item for item in reversed(s.calls)
                     if isinstance(item, tuple) and "up" in item[0])
    values = json.loads(kwargs["input"])["services"][s.runtime]["environment"]
    return {key: value.replace("$$", "$") for key, value in values.items()}


def test_live_ui_environment_overrides_stale_declaration_and_preserves_image_defaults(component_scenario):
    s = component_scenario
    live = {"NEXT_PUBLIC_VWORLD_API_KEY": "live$map-key", "PASSWORD": "live$password",
            "EMPTY": "", "PATH": "/current/bin"}
    s.records[s.runtime]["Config"]["Env"] = [f"{key}={value}" for key, value in live.items()]
    s.resolved["services"][s.runtime]["environment"] = {
        "NEXT_PUBLIC_VWORLD_API_KEY": "stale-map-key", "PASSWORD": "stale-password"}
    s.resolved["services"][s.runtime]["env_file"] = ["/stale.env"]
    s.metadata[paired.NEW]["Config"]["Env"] = ["NODE_ENV=production", "PATH=/image/bin", "NEW_DEFAULT=ok"]
    def recreate_environment():
        values = materialized_environment(s)
        s.records[s.runtime]["Config"]["Env"] = [f"{key}={value}" for key, value in values.items()]
    s.on_mutation = recreate_environment
    result = apply(s)
    expected = {"NODE_ENV": "production", "NEW_DEFAULT": "ok", **live}
    assert materialized_environment(s) == expected
    assert result["success"] is True and result["selected_environment_unchanged"] is True
    assert dict(value.split("=", 1) for value in s.records[s.runtime]["Config"]["Env"]) == expected
    _, kwargs = next(item for item in s.calls if isinstance(item, tuple) and "up" in item[0])
    assert "env_file" not in json.loads(kwargs["input"])["services"][s.runtime]
    for value in ["live$map-key", "live$password", "stale-map-key", "stale-password"]:
        assert value not in json.dumps(result)


@pytest.mark.parametrize("values", [None, "PASSWORD=x", ["missing-equals"], [1],
                                    ["=bad"], ["A=x", "A=y"], ["A=bad\x00value"]])
def test_invalid_live_environment_rejects_before_mutation(component_scenario, values):
    s = component_scenario
    s.records[s.runtime]["Config"]["Env"] = values
    with pytest.raises(release.ServiceReleaseError, match="환경"):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))
    assert release.read_current_release(s.service, root=s.root) == s.current


def test_live_environment_race_during_render_rejects_before_mutation(component_scenario, monkeypatch):
    s = component_scenario
    m = paired.candidate_module()
    original = m._render
    def raced(*args, **kwargs):
        value = original(*args, **kwargs)
        s.records[s.runtime]["Config"]["Env"] = ["PASSWORD=changed-during-render"]
        return value
    monkeypatch.setattr(m, "_render", raced)
    with pytest.raises(release.ServiceReleaseError, match="환경"):
        apply(s)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


def test_environment_drift_after_recreate_never_publishes(component_scenario):
    s = component_scenario
    s.on_mutation = lambda: s.records[s.runtime]["Config"].update(Env=["PASSWORD=unapproved-after"])
    result = apply(s)
    assert result["success"] is False and result["selected_environment_unchanged"] is False
    assert release.read_current_release(s.service, root=s.root) == s.current
    assert "unapproved-after" not in json.dumps(result)


@pytest.mark.parametrize("boundary", [1, 2])
def test_late_environment_drift_cannot_publish(component_scenario, monkeypatch, boundary):
    s = component_scenario
    m = paired.candidate_module()
    original = m._publish
    def publish(candidate, manifests, root, guard):
        calls = 0
        def raced():
            nonlocal calls
            calls += 1
            if calls == boundary:
                s.records[s.runtime]["Config"]["Env"] = ["PASSWORD=late-unapproved"]
            guard()
        return original(candidate, manifests, root, raced)
    monkeypatch.setattr(m, "_publish", publish)
    result = apply(s)
    assert result["success"] is False and result["selected_environment_unchanged"] is False
    assert release.read_current_release(s.service, root=s.root) == s.current
    assert "late-unapproved" not in json.dumps(result)


def test_live_and_candidate_environment_secrets_are_redacted(component_scenario, monkeypatch):
    import subprocess
    s = component_scenario
    m = paired.candidate_module()
    s.records[s.runtime]["Config"]["Env"] = ["PASSWORD=live$secret", "NEXT_PUBLIC_VWORLD_API_KEY=live-map-secret"]
    s.metadata[paired.NEW]["Config"]["Env"] = ["IMAGE_SECRET=image$secret"]
    original = m.frozen._run_mutation
    def failed(*args, **kwargs):
        completed, signals = original(*args, **kwargs)
        return subprocess.CompletedProcess(completed.args, 1, "",
            "live$secret live-map-secret image$secret"), signals
    monkeypatch.setattr(m.frozen, "_run_mutation", failed)
    result = apply(s)
    assert result["success"] is False
    for value in ["live$secret", "live-map-secret", "image$secret"]:
        assert value not in json.dumps(result)
    assert release.read_current_release(s.service, root=s.root) == s.current


def test_render_error_redacts_live_environment_before_binding(component_scenario, monkeypatch):
    import subprocess
    s = component_scenario
    m = paired.candidate_module()
    s.records[s.runtime]["Config"]["Env"] = ["PASSWORD=live$secret", "NEXT_PUBLIC_VWORLD_API_KEY=live-map-secret"]
    s.metadata[paired.NEW]["Config"]["Env"] = ["IMAGE_SECRET=image$secret"]
    monkeypatch.setattr(m, "_render", lambda args, **kwargs:
        subprocess.CompletedProcess(args, 1, "", "live$secret live-map-secret image$secret"))
    result = apply(s)
    assert result["success"] is False
    for value in ["live$secret", "live-map-secret", "image$secret"]:
        assert value not in json.dumps(result)
    assert not any("up" in item[0] for item in s.calls if isinstance(item, tuple))


@pytest.mark.parametrize("value", ["literal$secret", "literal$$secret", "literal$$$secret",
                                    "literal$$$$secret", "$VAR", "$" + "{VAR}", "", "a=b"])
@pytest.mark.parametrize("location", ["live", "candidate-default"])
def test_raw_dollar_values_survive_component_recreate(component_scenario, value, location):
    s = component_scenario
    key = "PASSWORD" if location == "live" else "NEW_SECRET"
    if location == "live":
        s.records[s.runtime]["Config"]["Env"] = [key + "=" + value]
    else:
        s.metadata[paired.NEW]["Config"]["Env"] = [key + "=" + value]
    def recreate():
        effective = materialized_environment(s)
        s.records[s.runtime]["Config"]["Env"] = [
            name + "=" + item for name, item in effective.items()]
    s.on_mutation = recreate
    result = apply(s)
    assert result["success"] is True
    assert materialized_environment(s)[key] == value
    assert dict(item.split("=", 1) for item in s.records[s.runtime]["Config"]["Env"])[key] == value
