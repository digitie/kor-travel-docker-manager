"""Weather 경계마다 전체 관측 한 번, 다음 경계의 변경과 환경 검증은 유지한다."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import test_weather_core_candidate as weather_tests
from docker.models.containers import ContainerCollection
from test_service_release_candidate import all_label_match
from test_weather_core_candidate import apply_weather
from test_weather_core_candidate import core_case as core_case
from test_weather_core_candidate import scenario as scenario

from kor_travel_docker_manager.services import service_release_candidate as candidate
from kor_travel_docker_manager.services import service_release_component_candidate as component
from kor_travel_docker_manager.services import service_release_weather_candidate as weather
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

weather_case = weather_tests.weather_case


def test_common_observes_54_containers_once_per_boundary(weather_case, monkeypatch):
    s = weather_case
    for index in range(54 - len(s.records)):
        record = copy.deepcopy(s.records["db"])
        record["Id"] = f"unselected-{index}"
        record["Config"]["Labels"] = {}
        s.records[f"unselected-{index}"] = record
    counts = {"lists": 0, "inspects": 0}
    starts, boundaries, snapshots = [], [], []

    def listing(**kwargs):
        assert kwargs["all"] is True
        counts["lists"] += 1
        records = list(s.records.values())
        if filters := kwargs.get("filters"):
            records = [record for record in records if all_label_match(record, filters["label"])]
        return [{"Id": record["Id"]} for record in records]

    def inspect_container(cid):
        counts["inspects"] += 1
        return copy.deepcopy(next(record for record in s.records.values() if record["Id"] == cid))

    monkeypatch.setattr(s.client.api, "containers", listing, raising=False)
    monkeypatch.setattr(s.client.api, "inspect_container", inspect_container, raising=False)
    s.client.containers = ContainerCollection(s.client)
    original_lease = weather.state.check_lease

    def lease(*args):
        original_lease(*args)
        starts.append(counts.copy())

    monkeypatch.setattr(weather.state, "check_lease", lease)
    original_environment = component._preserve_environment

    def preserve(*args, **kwargs):
        bind, check, values = original_environment(*args, **kwargs)
        runtime = args[2]

        def unchanged(records=None):
            if records is None:
                check()
            else:
                check(records)
            snapshots.append(records)
            if runtime == weather.ROLES["frontend"]:
                boundaries.append({key: counts[key] - starts[-1][key] for key in counts})
                assert all(rows is records for rows in snapshots[-3:])
                if records is not None:
                    assert len(records) == len(s.records)
        return bind, unchanged, values

    monkeypatch.setattr(component, "_preserve_environment", preserve)
    result = apply_weather(s)
    assert result["success"], result
    assert len(boundaries) >= 5
    assert all(row["lists"] == 1 for row in boundaries), boundaries
    # migration 생성 전에는 54개, 이후에는 그 경계의 전체 55개를 다시 inspect한다.
    assert boundaries[0]["inspects"] == 54
    assert all(row["inspects"] in {54, 55} for row in boundaries)
    assert any(row["inspects"] == 55 for row in boundaries)
    assert all(snapshots[index] is not snapshots[index - 3]
               for index in range(3, len(snapshots), 3))


@pytest.mark.parametrize("change", ["nonselected", "environment", "duplicate-id", "missing-id", "source"])
def test_next_common_boundary_observes_drift(weather_case, change):
    s = weather_case

    def mutate():
        if change == "nonselected":
            s.records["db"]["RestartCount"] += 1
        elif change == "environment":
            s.records[weather.ROLES["api"]]["Config"]["Env"].append("UNAPPROVED=changed")
        elif change == "duplicate-id":
            s.records["duplicate"] = copy.deepcopy(s.records["db"])
        elif change == "missing-id":
            s.records["db"].pop("Id")
        else:
            (s.source / "file").write_text("changed after first boundary")

    s.hooks["snapshot"] = mutate
    result = apply_weather(s)
    assert result["success"] is False
    assert result["phase"] == "snapshot_and_backup"
    assert result["error_type"] == "ServiceReleaseError"
    assert s.steps == ["factory", "stop", "snapshot", "backup"]
    assert weather.release.read_current_release("weather", root=s.root) == s.current


def environment_case():
    old, new = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    record = {"Id": "selected", "Image": old, "Config": {
        "Labels": {"com.docker.compose.project": "test", "com.docker.compose.service": "ui"},
        "Env": ["SETTING=before"],
    }}
    client = SimpleNamespace(
        containers=SimpleNamespace(list=Mock(side_effect=lambda **kwargs: [SimpleNamespace(attrs=record)])),
        images=SimpleNamespace(get=Mock(return_value=SimpleNamespace(attrs={"Config": {"Env": []}}))),
    )
    _, check, _ = component._preserve_environment(client, "test", "ui", new)
    return client, record, new, check


def test_environment_callback_without_records_always_reads_fresh_inventory():
    client, record, _, check = environment_case()
    assert client.containers.list.call_count == 1
    check()
    assert client.containers.list.call_count == 2
    record["Config"]["Env"] = ["SETTING=changed"]
    with pytest.raises(ServiceReleaseError, match="effective"):
        check()
    assert client.containers.list.call_count == 3


def test_environment_callback_accepts_approved_candidate_records_without_docker():
    client, record, new, check = environment_case()
    rows = candidate._inventory(client)
    rows[0]["Image"] = new
    check(rows)
    assert client.containers.list.call_count == 2
    assert record["Image"] != new


@pytest.mark.parametrize("change", ["project", "runtime", "image", "environment", "duplicate-selected",
                                    "duplicate-unselected-id", "missing-id", "empty-id", "nonstring-id", "missing-selected"])
def test_supplied_environment_records_reject_mismatch_and_duplicate_identity(change):
    client, record, _, check = environment_case()
    rows = [copy.deepcopy(record)]
    if change == "project":
        rows[0]["Config"]["Labels"]["com.docker.compose.project"] = "other"
    elif change == "runtime":
        rows[0]["Config"]["Labels"]["com.docker.compose.service"] = "other"
    elif change == "image":
        rows[0]["Image"] = "sha256:" + "3" * 64
    elif change == "environment":
        rows[0]["Config"]["Env"] = ["SETTING=changed"]
    elif change == "duplicate-selected":
        rows.append(copy.deepcopy(rows[0]))
        rows[1]["Id"] = "second-selected"
    elif change == "duplicate-unselected-id":
        other = {"Id": "unselected", "Config": {"Labels": {}}}
        rows.extend([other, copy.deepcopy(other)])
    elif change == "missing-id":
        rows[0].pop("Id")
    elif change == "empty-id":
        rows[0]["Id"] = ""
    elif change == "nonstring-id":
        rows[0]["Id"] = 42
    else:
        rows = []
    with pytest.raises(ServiceReleaseError):
        check(rows)
    assert client.containers.list.call_count == 1
