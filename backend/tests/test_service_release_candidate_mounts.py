"""Mounts 열거 순서만 허용하고 실제 비선택 변경은 SDK 전 경로에서 거부한다."""
from __future__ import annotations

import copy

import pytest
import test_service_release_candidate as paired
import test_service_release_component_candidate as component

from kor_travel_docker_manager.services import service_release as release

scenario = paired.scenario
component_scenario = component.component_scenario


def mounts():
    bind = {"Type": "bind", "Source": "/fixture/source", "Destination": "/fixture/target",
            "RW": True, "Mode": "rw,z", "Propagation": "rprivate",
            "Future": {"Options": {"label": "fixture"}, "Ordered": ["first", "second"]}}
    volume = {"Type": "volume", "Name": "fixture-volume", "Source": "/fixture/volume",
              "Destination": "/data", "Driver": "local", "RW": False, "Mode": "ro",
              "Propagation": ""}
    # 같은 객체의 중복 수도 관측 내용이다. set/dedup으로 바꾸지 않는다.
    return [bind, copy.deepcopy(bind), volume]


def prepare_mounts(s):
    for name in ["api", "db", "dagster"]:
        s.records[name]["Mounts"] = mounts()
        s.records[name]["Config"]["Env"] = ["FIRST=one", "SECOND=two"]
        s.records[name]["HostConfig"] = {"Binds": ["/one:/first", "/two:/second"]}


@pytest.fixture
def map_sdk(scenario):
    s = scenario
    prepare_mounts(s)
    module = paired.prepare(s)
    s.service, s.runtime = "map", paired.UI

    def invoke(execute=True):
        return module.apply_ui_candidate(
            "map", group="ui", image_id=paired.NEW, source_sha=s.sha,
            source_clone=s.source, root=s.root, config=s.config,
            client_factory=s.factory, execute=execute,
        )
    return s, module, invoke


@pytest.fixture
def component_sdk(component_scenario):
    s = component_scenario
    prepare_mounts(s)
    return s, paired.candidate_module(), lambda execute=True: component.apply(s, execute=execute)


def during_render(case, change):
    s, module, _ = case
    original = module._render

    def render(*args, **kwargs):
        result = original(*args, **kwargs)
        change()
        return result
    s.monkeypatch.setattr(module, "_render", render)


def check_order_only(case, boundary, execute):
    s, module, invoke = case
    original = copy.deepcopy(s.records)

    def reorder():
        for name in ["api", "db", "dagster"]:
            # JSON object의 키 삽입 순서도 정렬 key에 영향을 주면 안 된다.
            s.records[name]["Mounts"] = [dict(reversed(list(item.items())))
                                           for item in reversed(s.records[name]["Mounts"])]
            assert s.records[name]["Mounts"] != original[name]["Mounts"]
    if boundary == "render":
        during_render(case, reorder)
    elif boundary == "after-up":
        s.on_mutation = reorder
    else:
        publish = module._publish

        def before_publish(*args, **kwargs):
            reorder()
            return publish(*args, **kwargs)
        s.monkeypatch.setattr(module, "_publish", before_publish)
    result = invoke(execute=execute)
    assert result["success"] is True
    assert result["nonselected_unchanged"] is True
    current = release.read_current_release(s.service, root=s.root)
    if execute:
        assert result["result"] == "healthy"
        assert current["images"][s.runtime]["id"] == paired.NEW
        assert current["previous"] == s.current["release_directory"]
    else:
        assert result["result"] == "planned"
        assert current == s.current
        assert not any("up" in row[0] for row in s.calls if isinstance(row, tuple))
    for name in ["api", "db", "dagster"]:
        expected = original[name]
        expected["Mounts"] = s.records[name]["Mounts"]
        assert s.records[name] == expected


BOUNDARIES = [("render", False), ("render", True), ("after-up", True), ("publish", True)]


@pytest.mark.parametrize("boundary,execute", BOUNDARIES)
def test_map_sdk_accepts_mount_order_only(map_sdk, boundary, execute):
    check_order_only(map_sdk, boundary, execute)


@pytest.mark.parametrize("boundary,execute", BOUNDARIES)
def test_component_sdk_accepts_mount_order_only(component_sdk, boundary, execute):
    check_order_only(component_sdk, boundary, execute)


CHANGES = ["Source", "Destination", "RW", "Mode", "Propagation", "Type", "Name", "Driver",
           "added-field", "removed-field", "nested-field", "nested-list-order",
           "duplicate-added", "duplicate-removed", "mount-added", "mount-removed",
           "list-to-null", "list-to-missing", "null-to-empty", "missing-to-empty",
           "missing-to-null", "null-to-missing", "config-env-order", "host-binds-order"]


def change_record(record, change):
    if change in ["Source", "Destination", "RW", "Mode", "Propagation", "Type"]:
        record["Mounts"][0][change] = False if change == "RW" else "changed"
    elif change in ["Name", "Driver"]:
        record["Mounts"][-1][change] = "changed"
    elif change == "added-field":
        record["Mounts"][0]["NewField"] = {"preserve": ["new", "value"]}
    elif change == "removed-field":
        del record["Mounts"][0]["Mode"]
    elif change == "nested-field":
        record["Mounts"][0]["Future"]["Options"]["label"] = "changed"
    elif change == "nested-list-order":
        record["Mounts"][0]["Future"]["Ordered"].reverse()
    elif change == "duplicate-added":
        record["Mounts"].append(copy.deepcopy(record["Mounts"][0]))
    elif change == "duplicate-removed":
        record["Mounts"].pop(1)
    elif change == "mount-added":
        record["Mounts"].append({"Type": "tmpfs", "Destination": "/new", "RW": True})
    elif change == "mount-removed":
        record["Mounts"].pop()
    elif change in ["list-to-null", "missing-to-null"]:
        record["Mounts"] = None
    elif change in ["list-to-missing", "null-to-missing"]:
        record.pop("Mounts", None)
    elif change in ["null-to-empty", "missing-to-empty"]:
        record["Mounts"] = []
    elif change == "config-env-order":
        record["Config"]["Env"].reverse()
    else:
        assert change == "host-binds-order"
        record["HostConfig"]["Binds"].reverse()


def check_real_change_rejects(case, change):
    s, _, invoke = case
    record = s.records["api"]
    if change.startswith("missing-"):
        record.pop("Mounts")
    elif change.startswith("null-"):
        record["Mounts"] = None
    during_render(case, lambda: change_record(record, change))
    with pytest.raises(release.ServiceReleaseError, match="비선택"):
        invoke()
    assert release.read_current_release(s.service, root=s.root) == s.current
    assert not any("up" in row[0] for row in s.calls if isinstance(row, tuple))


@pytest.mark.parametrize("change", CHANGES)
def test_map_sdk_rejects_actual_nonselected_change(map_sdk, change):
    check_real_change_rejects(map_sdk, change)


@pytest.mark.parametrize("change", CHANGES)
def test_component_sdk_rejects_actual_nonselected_change(component_sdk, change):
    check_real_change_rejects(component_sdk, change)


def test_nonselected_normalization_does_not_mutate_inspect_or_nested_arrays():
    record = {"Id": "other", "Image": paired.OTHER,
              "State": {"StartedAt": "fixture", "Running": True}, "Mounts": mounts(),
              "Config": {"Env": ["SECOND=two", "FIRST=one"]},
              "HostConfig": {"Binds": ["/two:/second", "/one:/first"]}}
    original = copy.deepcopy(record)
    module = paired.candidate_module()
    module._nonselected([record], "selected", "ui")
    assert record == original
